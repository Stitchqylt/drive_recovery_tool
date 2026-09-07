"""drive_rescue.contract - Strict typed contract schemas and validation."""

from .contract import (
    CONTRACT_VERSION,
    SkillStatus,
    FilePayload,
    ProcessedFileResult,
    AuditEntry,
    ExecutionMetrics,
    SkillError,
    SkillHealth,
    SkillInput,
    SkillOutput,
    validate_input_contract,
    validate_output_contract,
    generate_json_schema,
)

__all__ = [
    "CONTRACT_VERSION",
    "SkillStatus",
    "FilePayload",
    "ProcessedFileResult",
    "AuditEntry",
    "ExecutionMetrics",
    "SkillError",
    "SkillHealth",
    "SkillInput",
    "SkillOutput",
    "validate_input_contract",
    "validate_output_contract",
    "generate_json_schema",
]
