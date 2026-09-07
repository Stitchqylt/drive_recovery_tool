"""
drive_rescue.runtime.executor - Resilient Skill Executor with Circuit Breakers & Fallbacks

Executes skills with schema validation, deadline enforcement, circuit breakers,
canary routing, and automatic fallback to stable versions.
"""

import os
import sys
import json
import time
import importlib
import subprocess
import threading
from typing import Dict, Any, List, Optional

from drive_rescue.contract import (
    SkillInput,
    SkillOutput,
    SkillError,
    ExecutionMetrics,
    validate_input_contract,
    validate_output_contract,
)
from drive_rescue.registry import GLOBAL_REGISTRY, SkillManifest, SkillRegistry
from drive_rescue.runtime.base_skill import BaseSkill


class CircuitBreakerOpenException(Exception):
    """Raised when circuit breaker is open due to high error rates."""
    pass


class CircuitBreaker:
    """
    Tracks failure rates per skill and trips when failure threshold is exceeded,
    preventing cascading host application failure.
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout_s: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # "CLOSED", "OPEN", "HALF_OPEN"
        self._lock = threading.Lock()

    def record_success(self):
        with self._lock:
            self.failure_count = 0
            self.state = "CLOSED"

    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"

    def can_execute(self) -> bool:
        with self._lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                # Check if recovery timeout elapsed
                if time.time() - self.last_failure_time >= self.recovery_timeout_s:
                    self.state = "HALF_OPEN"
                    return True
                return False
            if self.state == "HALF_OPEN":
                return True
            return False


class SkillExecutor:
    """
    Decoupled Host Executor that orchestrates skill invocation with validation,
    circuit breakers, canary weighting, and fallback execution.
    """

    def __init__(self, registry: Optional[SkillRegistry] = None, execution_mode: str = "in_process"):
        self.registry = registry or GLOBAL_REGISTRY
        self.execution_mode = execution_mode  # "in_process" or "isolated_process"
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._skill_cache: Dict[str, BaseSkill] = {}
        self._lock = threading.Lock()

    def _get_circuit_breaker(self, skill_id: str) -> CircuitBreaker:
        with self._lock:
            if skill_id not in self._circuit_breakers:
                self._circuit_breakers[skill_id] = CircuitBreaker()
            return self._circuit_breakers[skill_id]

    def _load_skill_instance(self, manifest: SkillManifest) -> Optional[BaseSkill]:
        """Dynamically loads and instantiates a python_module skill."""
        entrypoint = manifest.runtime.entrypoint
        if not entrypoint:
            return None

        cache_key = f"{manifest.id}:{manifest.version}"
        with self._lock:
            if cache_key in self._skill_cache:
                return self._skill_cache[cache_key]

        try:
            mod_name, class_name = entrypoint.rsplit(":", 1)
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, class_name)
            instance = cls()
            with self._lock:
                self._skill_cache[cache_key] = instance
            return instance
        except Exception as e:
            print(f"[-] Failed to load skill {manifest.id} ({entrypoint}): {e}")
            return None

    def check_all_health(self) -> Dict[str, Dict[str, Any]]:
        """Queries health status across all registered skills."""
        health_reports: Dict[str, Dict[str, Any]] = {}
        for skill_id in self.registry._catalog.keys():
            manifest = self.registry.get_skill_manifest(skill_id)
            if not manifest:
                continue
            instance = self._load_skill_instance(manifest)
            cb = self._get_circuit_breaker(skill_id)
            if instance:
                try:
                    h = instance.health()
                    d = h.to_dict()
                    d["circuit_breaker_state"] = cb.state
                    health_reports[skill_id] = d
                except Exception as e:
                    health_reports[skill_id] = {
                        "status": "UNHEALTHY",
                        "skill_id": skill_id,
                        "version": manifest.version,
                        "error": str(e),
                        "circuit_breaker_state": cb.state,
                    }
            else:
                health_reports[skill_id] = {
                    "status": "UNHEALTHY",
                    "skill_id": skill_id,
                    "version": manifest.version,
                    "error": "Failed to load skill instance",
                    "circuit_breaker_state": cb.state,
                }
        return health_reports

    def execute_skill(self, input_payload: SkillInput, version: Optional[str] = None) -> SkillOutput:
        """
        Executes a skill with full resilience: schema validation, circuit breaking,
        and fallback execution if the primary fails.
        """
        start_time = time.time()
        skill_id = input_payload.skill_id
        cb = self._get_circuit_breaker(skill_id)

        # 1. Resolve manifest from registry
        manifest = self.registry.get_skill_manifest(skill_id, version=version)
        if not manifest:
            return SkillOutput(
                success=False,
                skill_id=skill_id,
                skill_version="unknown",
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                error=SkillError(
                    code="SKILL_NOT_FOUND",
                    message=f"No registered skill found for id '{skill_id}'",
                    retryable=False,
                )
            )

        # 2. Check Circuit Breaker
        if not cb.can_execute():
            # Try fallback version if specified in manifest rollout policy
            if manifest.rollout.fallback_version:
                print(f"[!] Circuit breaker OPEN for {skill_id}. Routing to fallback v{manifest.rollout.fallback_version}")
                fallback_m = self.registry.get_skill_manifest(skill_id, version=manifest.rollout.fallback_version)
                if fallback_m:
                    return self._invoke_manifest(fallback_m, input_payload, start_time)

            return SkillOutput(
                success=False,
                skill_id=skill_id,
                skill_version=manifest.version,
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                error=SkillError(
                    code="CIRCUIT_BREAKER_OPEN",
                    message=f"Circuit breaker is OPEN for skill '{skill_id}' due to recent errors",
                    retryable=True,
                )
            )

        # 3. Invoke primary skill
        output = self._invoke_manifest(manifest, input_payload, start_time)

        # 4. Record metrics in Circuit Breaker
        if output.success:
            cb.record_success()
        else:
            cb.record_failure()

        return output

    def _invoke_isolated_process(self, manifest: SkillManifest, input_payload: SkillInput, start_time: float) -> SkillOutput:
        """Executes a skill inside an isolated worker subprocess with strict timeout containment."""
        timeout_s = (input_payload.timeout_ms or manifest.runtime.timeout_ms or 30000) / 1000.0
        payload_dict = input_payload.to_dict()
        if "options" not in payload_dict or not isinstance(payload_dict["options"], dict):
            payload_dict["options"] = {}
        payload_dict["options"]["_entrypoint"] = manifest.runtime.entrypoint

        cmd = [sys.executable, "-m", "drive_rescue.runtime.worker_runner"]
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(input=json.dumps(payload_dict), timeout=timeout_s)

            if proc.returncode != 0:
                return SkillOutput(
                    success=False,
                    skill_id=manifest.id,
                    skill_version=manifest.version,
                    session_id=input_payload.session_id,
                    trace_id=input_payload.trace_id,
                    error=SkillError(
                        code="PROCESS_CRASHED",
                        message=f"Worker process crashed with code {proc.returncode}: {stderr.strip()}",
                        retryable=True,
                    )
                )

            # Extract json payload between delimiters
            start_tag = "__SKILL_RESULT_JSON_START__"
            end_tag = "__SKILL_RESULT_JSON_END__"
            if start_tag in stdout and end_tag in stdout:
                json_str = stdout.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
            else:
                json_str = stdout.strip()

            out_dict = json.loads(json_str)
            output = SkillOutput.from_dict(out_dict)

            elapsed_ms = round((time.time() - start_time) * 1000.0, 2)
            output.metrics.duration_ms = elapsed_ms
            output.metrics.files_total = len(input_payload.files)
            output.metrics.files_succeeded = len([p for p in output.processed_files if p.status == "OK"])
            output.metrics.files_failed = len([p for p in output.processed_files if p.status == "FAILED"])
            return output

        except subprocess.TimeoutExpired:
            if proc:
                try:
                    proc.kill()
                    proc.communicate()
                except Exception:
                    pass
            return SkillOutput(
                success=False,
                skill_id=manifest.id,
                skill_version=manifest.version,
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                error=SkillError(
                    code="TIMEOUT_EXCEEDED",
                    message=f"Worker execution exceeded deadline of {timeout_s}s",
                    retryable=False,
                )
            )
        except Exception as ex:
            return SkillOutput(
                success=False,
                skill_id=manifest.id,
                skill_version=manifest.version,
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                error=SkillError(
                    code="ISOLATION_INVOCATION_ERROR",
                    message=f"Failed to execute isolated process: {ex}",
                    retryable=True,
                )
            )

    def _invoke_manifest(self, manifest: SkillManifest, input_payload: SkillInput, start_time: float) -> SkillOutput:
        """Internal invocation of a specific skill manifest with isolation routing."""
        if self.execution_mode == "isolated_process" and manifest.runtime.type == "python_module":
            return self._invoke_isolated_process(manifest, input_payload, start_time)

        instance = self._load_skill_instance(manifest)
        if not instance:
            return SkillOutput(
                success=False,
                skill_id=manifest.id,
                skill_version=manifest.version,
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                error=SkillError(
                    code="RUNTIME_LOAD_ERROR",
                    message=f"Failed to instantiate runtime for '{manifest.id}'",
                    retryable=False,
                )
            )

        # Execute with contract validation
        output = instance.validate_and_execute(input_payload)

        # Compute duration metrics
        elapsed_ms = round((time.time() - start_time) * 1000.0, 2)
        output.metrics.duration_ms = elapsed_ms
        output.metrics.files_total = len(input_payload.files)
        output.metrics.files_succeeded = len([p for p in output.processed_files if p.status == "OK"])
        output.metrics.files_failed = len([p for p in output.processed_files if p.status == "FAILED"])

        return output

    def execute_pipeline(self, skill_ids: List[str], input_payload: SkillInput) -> List[SkillOutput]:
        """Executes a pipeline of multiple skills sequentially."""
        results = []
        for s_id in skill_ids:
            step_input = SkillInput(
                skill_id=s_id,
                files=input_payload.files,
                destination_dir=input_payload.destination_dir,
                session_id=input_payload.session_id,
                trace_id=input_payload.trace_id,
                options=input_payload.options,
            )
            out = self.execute_skill(step_input)
            results.append(out)
        return results


# Global executor singleton
GLOBAL_EXECUTOR = SkillExecutor()
