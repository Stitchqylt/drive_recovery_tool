"""
tests/test_e2e_smoke.py - End-to-End Smoke & Sandboxed Isolation Tests for Skills Runtime
"""

import os
import shutil
import tempfile
import unittest

from drive_rescue.contract import SkillInput, FilePayload, SkillOutput
from drive_rescue.registry import SkillRegistry
from drive_rescue.runtime.executor import SkillExecutor


class TestEndToEndSkillsSmoke(unittest.TestCase):
    """Verifies end-to-end recovery pipeline under isolated worker process mode."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="dr_e2e_")
        cls.in_dir = os.path.join(cls.test_dir, "input")
        cls.out_dir = os.path.join(cls.test_dir, "output")
        os.makedirs(cls.in_dir, exist_ok=True)
        os.makedirs(cls.out_dir, exist_ok=True)

        # Create test corrupted file
        cls.damaged_png = os.path.join(cls.in_dir, "corrupted_logo.png.partial")
        with open(cls.damaged_png, "wb") as f:
            f.write(b"BAD_HEADER\x00\x00\x00\rIHDR" + b"\x00" * 40 + b"IEND\xaeB`\x82")

        cls.registry = SkillRegistry()
        skills_base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
        cls.registry.discover_directory(skills_base)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_isolated_worker_execution(self):
        """Verify that skill execution in isolated child process runs and repairs file."""
        executor = SkillExecutor(registry=self.registry, execution_mode="isolated_process")

        payload = SkillInput(
            skill_id="repair-corrupted-files",
            files=[
                FilePayload(id=101, name="corrupted_logo.png.partial", real_path=self.damaged_png, status="Partial")
            ],
            destination_dir=self.out_dir,
            session_id="session-e2e-101",
            trace_id="trace-smoke-999",
            timeout_ms=10000,
        )

        output = executor.execute_skill(payload)
        self.assertTrue(output.success)
        self.assertEqual(output.session_id, "session-e2e-101")
        self.assertEqual(output.trace_id, "trace-smoke-999")
        self.assertEqual(output.repaired_count, 1)
        self.assertEqual(len(output.processed_files), 1)

        # Check repaired file on disk
        out_file = output.processed_files[0].output_path
        self.assertTrue(os.path.exists(out_file))
        with open(out_file, "rb") as f:
            header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n")

    def test_isolated_worker_pipeline_execution(self):
        """Verify sequential multi-skill pipeline execution under process isolation."""
        executor = SkillExecutor(registry=self.registry, execution_mode="isolated_process")

        payload = SkillInput(
            skill_id="pipeline-e2e",
            files=[
                FilePayload(id=102, name="corrupted_logo.png.partial", real_path=self.damaged_png, status="Partial")
            ],
            destination_dir=os.path.join(self.out_dir, "pipeline_dest"),
            session_id="session-pipe-01",
            trace_id="trace-pipe-01",
        )

        pipeline_skills = ["repair-corrupted-files", "smart-sorter-reorganizer"]
        outputs = executor.execute_pipeline(pipeline_skills, payload)

        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(o.success for o in outputs))
        self.assertEqual(outputs[0].skill_id, "repair-corrupted-files")
        self.assertEqual(outputs[1].skill_id, "smart-sorter-reorganizer")

    def test_isolated_worker_timeout_containment(self):
        """Verify that a worker taking too long is terminated without crashing host."""
        executor = SkillExecutor(registry=self.registry, execution_mode="isolated_process")

        # Extremely low timeout of 1ms on large file processing
        payload = SkillInput(
            skill_id="repair-corrupted-files",
            files=[
                FilePayload(id=103, name="large_corrupt.dat", status="Partial")
            ],
            destination_dir=self.out_dir,
            timeout_ms=1,  # 1ms timeout forces timeout kill
        )

        output = executor.execute_skill(payload)
        self.assertFalse(output.success)
        self.assertIsNotNone(output.error)
        self.assertEqual(output.error.code, "TIMEOUT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
