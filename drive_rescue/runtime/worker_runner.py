"""
drive_rescue.runtime.worker_runner - Standalone Process Entrypoint for Isolated Skill Execution

Executes skills in a separate, isolated child process for fault tolerance and privilege containment.
I/O is exchanged over JSON stdin/stdout conforming to Contract v1.0.0.
"""

import sys
import json
import importlib
import os

# Ensure repository root is in sys.path
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from drive_rescue.contract import SkillInput, SkillOutput, SkillError, ExecutionMetrics


def _send_output(output: SkillOutput):
    payload_str = json.dumps(output.to_dict())
    sys.stdout.write(f"\n__SKILL_RESULT_JSON_START__\n{payload_str}\n__SKILL_RESULT_JSON_END__\n")
    sys.stdout.flush()


def run_worker():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            out = SkillOutput(
                success=False,
                skill_id="unknown",
                skill_version="unknown",
                error=SkillError(code="EMPTY_PAYLOAD", message="Worker process received empty input payload"),
            )
            _send_output(out)
            return

        input_data = json.loads(raw_input)
        payload = SkillInput.from_dict(input_data)

        entrypoint = input_data.get("options", {}).get("_entrypoint")
        if not entrypoint:
            out = SkillOutput(
                success=False,
                skill_id=payload.skill_id,
                skill_version="unknown",
                session_id=payload.session_id,
                trace_id=payload.trace_id,
                error=SkillError(code="MISSING_ENTRYPOINT", message="Runtime entrypoint was not provided to worker"),
            )
            _send_output(out)
            return

        mod_name, class_name = entrypoint.rsplit(":", 1)
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, class_name)
        instance = cls()

        output = instance.validate_and_execute(payload)
        _send_output(output)
    except Exception as ex:
        out = SkillOutput(
            success=False,
            skill_id="unknown",
            skill_version="unknown",
            error=SkillError(code="WORKER_EXCEPTION", message=str(ex)),
        )
        _send_output(out)


if __name__ == "__main__":
    run_worker()
