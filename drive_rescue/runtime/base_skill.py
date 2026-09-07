"""
drive_rescue.runtime.base_skill - Standard Base Class for All Decoupled Recovery Skills
"""

import time
import abc
from typing import Dict, Any, List, Optional

from drive_rescue.contract import (
    CONTRACT_VERSION,
    SkillInput,
    SkillOutput,
    SkillHealth,
    SkillError,
    validate_input_contract,
    validate_output_contract,
)


class BaseSkill(abc.ABC):
    """
    Abstract base class that all recovery skills implement.
    Guarantees conformance to Contract v1.0.0.
    """

    def __init__(self, skill_id: str, version: str = "1.0.0"):
        self.skill_id = skill_id
        self.version = version
        self.start_time = time.time()

    @abc.abstractmethod
    def execute(self, payload: SkillInput) -> SkillOutput:
        """Executes the skill against the input payload and returns a compliant SkillOutput."""
        pass

    def health(self) -> SkillHealth:
        """Returns health status and diagnostic capabilities."""
        return SkillHealth(
            status="OK",
            skill_id=self.skill_id,
            version=self.version,
            contract_version=CONTRACT_VERSION,
            uptime_seconds=round(time.time() - self.start_time, 2),
            supported_formats=[],
        )

    def validate_and_execute(self, payload: SkillInput) -> SkillOutput:
        """Validates inputs against schema, executes skill, and validates outputs."""
        input_errors = validate_input_contract(payload)
        if input_errors:
            return SkillOutput(
                success=False,
                skill_id=self.skill_id,
                skill_version=self.version,
                session_id=payload.session_id,
                trace_id=payload.trace_id,
                error=SkillError(
                    code="INVALID_INPUT_SCHEMA",
                    message="; ".join(input_errors),
                    retryable=False,
                )
            )

        try:
            output = self.execute(payload)
        except Exception as ex:
            return SkillOutput(
                success=False,
                skill_id=self.skill_id,
                skill_version=self.version,
                session_id=payload.session_id,
                trace_id=payload.trace_id,
                error=SkillError(
                    code="EXECUTION_EXCEPTION",
                    message=str(ex),
                    retryable=True,
                )
            )

        output_errors = validate_output_contract(output)
        if output_errors:
            output.success = False
            output.error = SkillError(
                code="INVALID_OUTPUT_SCHEMA",
                message="; ".join(output_errors),
                retryable=False,
            )

        return output
