"""
drive_rescue.contract.contract - Strict, Versioned Contract (Schema) for Recovery Skills (v1.0.0)

Provides zero-dependency typed data contracts, validation schemas, and serialization
for inputs, outputs, error models, health checks, and execution metrics.
"""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Union


CONTRACT_VERSION = "v1.0.0"


class SkillStatus:
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    SKIPPED = "SKIPPED"


@dataclass
class FilePayload:
    """Represents an input file subject to recovery/repair."""
    id: int
    name: str
    real_path: Optional[str] = None
    path: str = ""
    size_bytes: int = 0
    status: str = "Good"  # "Good", "Partial", "Failed"
    mime_type: str = "application/octet-stream"
    is_folder: bool = False
    is_app: bool = False
    attributes: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FilePayload':
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "unnamed")),
            real_path=data.get("real_path"),
            path=str(data.get("path", "")),
            size_bytes=int(data.get("size_bytes", data.get("raw_size", 0))),
            status=str(data.get("status", "Good")),
            mime_type=str(data.get("mime_type", data.get("type", "application/octet-stream"))),
            is_folder=bool(data.get("is_folder", False)),
            is_app=bool(data.get("is_app", False)),
            attributes=dict(data.get("attributes", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessedFileResult:
    """Result of a single file processing operation."""
    file_id: int
    original_name: str
    output_name: str
    output_path: str
    status: str  # "OK", "PARTIAL", "FAILED", "QUARANTINED", "SKIPPED"
    bytes_recovered: int = 0
    actions_taken: List[str] = field(default_factory=list)
    sha256_hash: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEntry:
    """Forensic timeline event entry."""
    timestamp: str
    file_id: int
    action: str
    detail: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionMetrics:
    """Performance and telemetry metrics for a skill invocation."""
    duration_ms: float = 0.0
    bytes_processed: int = 0
    files_total: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    memory_peak_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillError:
    """Standardized error model for skill execution failures."""
    code: str
    message: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillHealth:
    """Health check response for a skill runtime."""
    status: str  # "OK", "DEGRADED", "UNHEALTHY"
    skill_id: str
    version: str
    contract_version: str = CONTRACT_VERSION
    uptime_seconds: float = 0.0
    supported_formats: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillInput:
    """Strict typed input specification for all recovery skills."""
    skill_id: str
    files: List[FilePayload]
    destination_dir: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    contract_version: str = CONTRACT_VERSION
    timeout_ms: int = 30000
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillInput':
        files = [FilePayload.from_dict(f) if isinstance(f, dict) else f for f in data.get("files", [])]
        return cls(
            skill_id=str(data.get("skill_id", "")),
            files=files,
            destination_dir=str(data.get("destination_dir", "recovered_files")),
            session_id=str(data.get("session_id", uuid.uuid4())),
            trace_id=str(data.get("trace_id", uuid.uuid4())),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
            timeout_ms=int(data.get("timeout_ms", 30000)),
            options=dict(data.get("options", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in self.files]
        return d


@dataclass
class SkillOutput:
    """Strict typed output specification for all recovery skills."""
    success: bool
    skill_id: str
    skill_version: str
    contract_version: str = CONTRACT_VERSION
    session_id: str = ""
    trace_id: str = ""
    processed_files: List[ProcessedFileResult] = field(default_factory=list)
    repaired_count: int = 0
    quarantined_count: int = 0
    sorted_count: int = 0
    audit_log: List[AuditEntry] = field(default_factory=list)
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: Optional[SkillError] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillOutput':
        processed = [
            ProcessedFileResult(**p) if isinstance(p, dict) else p
            for p in data.get("processed_files", [])
        ]
        audit = [
            AuditEntry(**a) if isinstance(a, dict) else a
            for a in data.get("audit_log", [])
        ]
        metrics = (
            ExecutionMetrics(**data["metrics"])
            if isinstance(data.get("metrics"), dict)
            else ExecutionMetrics()
        )
        err = (
            SkillError(**data["error"])
            if isinstance(data.get("error"), dict)
            else None
        )
        return cls(
            success=bool(data.get("success", False)),
            skill_id=str(data.get("skill_id", "")),
            skill_version=str(data.get("skill_version", "1.0.0")),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
            session_id=str(data.get("session_id", "")),
            trace_id=str(data.get("trace_id", "")),
            processed_files=processed,
            repaired_count=int(data.get("repaired_count", 0)),
            quarantined_count=int(data.get("quarantined_count", 0)),
            sorted_count=int(data.get("sorted_count", 0)),
            audit_log=audit,
            metrics=metrics,
            error=err,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["processed_files"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.processed_files]
        d["audit_log"] = [a.to_dict() if hasattr(a, "to_dict") else a for a in self.audit_log]
        d["metrics"] = self.metrics.to_dict() if hasattr(self.metrics, "to_dict") else self.metrics
        d["error"] = self.error.to_dict() if self.error and hasattr(self.error, "to_dict") else self.error
        return d


def validate_input_contract(data: Union[Dict[str, Any], SkillInput]) -> List[str]:
    """Validates raw dict or SkillInput against Contract v1 schema rules."""
    errors = []
    if isinstance(data, SkillInput):
        data = data.to_dict()

    if not isinstance(data, dict):
        return ["Input payload must be a JSON object"]

    if not data.get("skill_id"):
        errors.append("Field 'skill_id' is required and cannot be empty")

    if not data.get("destination_dir"):
        errors.append("Field 'destination_dir' is required")

    files = data.get("files")
    if not isinstance(files, list):
        errors.append("Field 'files' must be an array")
    else:
        for idx, f in enumerate(files):
            if not isinstance(f, dict):
                errors.append(f"files[{idx}] must be an object")
                continue
            if "name" not in f:
                errors.append(f"files[{idx}].name is required")
            if "id" not in f:
                errors.append(f"files[{idx}].id is required")

    return errors


def validate_output_contract(data: Union[Dict[str, Any], SkillOutput]) -> List[str]:
    """Validates raw dict or SkillOutput against Contract v1 schema rules."""
    errors = []
    if isinstance(data, SkillOutput):
        data = data.to_dict()

    if not isinstance(data, dict):
        return ["Output payload must be a JSON object"]

    for req_field in ["success", "skill_id", "skill_version", "contract_version"]:
        if req_field not in data:
            errors.append(f"Output field '{req_field}' is required")

    if not isinstance(data.get("processed_files", []), list):
        errors.append("Field 'processed_files' must be an array")

    return errors


def generate_json_schema() -> Dict[str, Any]:
    """Returns canonical JSON Schema for Contract v1."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "DriveRescueSkillContract",
        "version": CONTRACT_VERSION,
        "type": "object",
        "definitions": {
            "SkillInput": {
                "type": "object",
                "required": ["skill_id", "files", "destination_dir"],
                "properties": {
                    "skill_id": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "name"],
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                                "real_path": {"type": ["string", "null"]},
                                "path": {"type": "string"},
                                "size_bytes": {"type": "integer"},
                                "status": {"type": "string", "enum": ["Good", "Partial", "Failed"]},
                                "mime_type": {"type": "string"},
                                "is_folder": {"type": "boolean"},
                                "is_app": {"type": "boolean"}
                            }
                        }
                    },
                    "destination_dir": {"type": "string"},
                    "session_id": {"type": "string"},
                    "trace_id": {"type": "string"},
                    "contract_version": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                    "options": {"type": "object"}
                }
            },
            "SkillOutput": {
                "type": "object",
                "required": ["success", "skill_id", "skill_version", "contract_version", "processed_files"],
                "properties": {
                    "success": {"type": "boolean"},
                    "skill_id": {"type": "string"},
                    "skill_version": {"type": "string"},
                    "contract_version": {"type": "string"},
                    "processed_files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["file_id", "original_name", "output_path", "status"],
                            "properties": {
                                "file_id": {"type": "integer"},
                                "original_name": {"type": "string"},
                                "output_name": {"type": "string"},
                                "output_path": {"type": "string"},
                                "status": {"type": "string"},
                                "bytes_recovered": {"type": "integer"},
                                "actions_taken": {"type": "array", "items": {"type": "string"}}
                            }
                        }
                    },
                    "repaired_count": {"type": "integer"},
                    "quarantined_count": {"type": "integer"},
                    "sorted_count": {"type": "integer"}
                }
            }
        }
    }
