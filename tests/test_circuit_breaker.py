"""
tests/test_circuit_breaker.py - Comprehensive Unit & Resilience Tests for Circuit Breakers & Fallback Routing
"""

import time
import unittest
from unittest.mock import MagicMock

from drive_rescue.contract import SkillInput, FilePayload, SkillOutput, SkillError
from drive_rescue.registry import SkillRegistry, SkillManifest, SkillRolloutSpec, SkillRuntimeSpec
from drive_rescue.runtime.executor import CircuitBreaker, SkillExecutor
from drive_rescue.runtime.base_skill import BaseSkill


class DummyFailingSkill(BaseSkill):
    def __init__(self, skill_id="dummy-failing", version="2.0.0"):
        super().__init__(skill_id=skill_id, version=version)

    def execute(self, payload: SkillInput) -> SkillOutput:
        return SkillOutput(
            success=False,
            skill_id=self.skill_id,
            skill_version=self.version,
            session_id=payload.session_id,
            error=SkillError(code="SIMULATED_FAILURE", message="Simulated downstream skill error"),
        )


class DummyWorkingSkill(BaseSkill):
    def __init__(self, skill_id="dummy-failing", version="1.0.0"):
        super().__init__(skill_id=skill_id, version=version)

    def execute(self, payload: SkillInput) -> SkillOutput:
        return SkillOutput(
            success=True,
            skill_id=self.skill_id,
            skill_version=self.version,
            session_id=payload.session_id,
            repaired_count=len(payload.files),
        )


class TestCircuitBreaker(unittest.TestCase):
    """Verifies state machine transitions for the zero-dependency CircuitBreaker."""

    def test_circuit_breaker_transitions(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=0.1)
        self.assertEqual(cb.state, "CLOSED")
        self.assertTrue(cb.can_execute())

        # Record 2 failures -> Still CLOSED
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "CLOSED")
        self.assertTrue(cb.can_execute())

        # 3rd failure -> Trips to OPEN
        cb.record_failure()
        self.assertEqual(cb.state, "OPEN")
        self.assertFalse(cb.can_execute())

        # Wait for recovery timeout
        time.sleep(0.12)
        # Next check should transition to HALF_OPEN
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, "HALF_OPEN")

        # Success resets to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failure_count, 0)
        self.assertTrue(cb.can_execute())


class TestExecutorCircuitBreakerAndFallback(unittest.TestCase):
    """Verifies that SkillExecutor trips circuit breakers and routes to fallback versions."""

    def setUp(self):
        self.registry = SkillRegistry()

        # Primary failing version (2.0.0 canary) with fallback to stable (1.0.0)
        self.m_primary = SkillManifest(
            id="faulty-skill",
            name="Faulty Skill",
            version="2.0.0",
            description="Testing fallback",
            rollout=SkillRolloutSpec(strategy="canary", weight=100, fallback_version="1.0.0", is_active=True),
            runtime=SkillRuntimeSpec(type="python_module", entrypoint="tests.test_circuit_breaker:DummyFailingSkill"),
        )

        # Stable fallback version (1.0.0)
        self.m_fallback = SkillManifest(
            id="faulty-skill",
            name="Faulty Skill Stable",
            version="1.0.0",
            description="Testing fallback stable",
            rollout=SkillRolloutSpec(strategy="direct", weight=0, fallback_version=None, is_active=True),
            runtime=SkillRuntimeSpec(type="python_module", entrypoint="tests.test_circuit_breaker:DummyWorkingSkill"),
        )

        self.registry.register_manifest(self.m_primary)
        self.registry.register_manifest(self.m_fallback)
        self.executor = SkillExecutor(registry=self.registry)

    def test_circuit_tripping_and_fallback_routing(self):
        cb = self.executor._get_circuit_breaker("faulty-skill")
        cb.failure_threshold = 2
        cb.recovery_timeout_s = 5.0  # long timeout so it stays OPEN

        test_input = SkillInput(
            skill_id="faulty-skill",
            files=[FilePayload(id=1, name="test.dat", status="Partial")],
            destination_dir="/tmp/test_out",
        )

        # First invocation -> fails, records failure #1
        out1 = self.executor.execute_skill(test_input, version="2.0.0")
        self.assertFalse(out1.success)
        self.assertEqual(out1.skill_version, "2.0.0")
        self.assertEqual(cb.state, "CLOSED")

        # Second invocation -> fails, records failure #2 -> Trips to OPEN
        out2 = self.executor.execute_skill(test_input, version="2.0.0")
        self.assertFalse(out2.success)
        self.assertEqual(cb.state, "OPEN")

        # Third invocation with primary version:
        # Since CB is OPEN and fallback_version="1.0.0" is specified, it should route to v1.0.0!
        out3 = self.executor.execute_skill(test_input)
        self.assertTrue(out3.success)
        self.assertEqual(out3.skill_version, "1.0.0")
        self.assertEqual(out3.repaired_count, 1)


if __name__ == "__main__":
    unittest.main()
