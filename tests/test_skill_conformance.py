"""
tests/test_skill_conformance.py - Universal Contract v1 Conformance Test Suite for All Decoupled Skills

Tests all 10 standalone recovery skills against Contract v1 specifications:
1. Manifest structure and trigger definitions.
2. Dynamic instantiation of BaseSkill entrypoints.
3. SkillHealth check telemetry.
4. Input / Output schema contract validation.
5. End-to-end file recovery execution across various corrupted file scenarios.
6. Error handling and invalid payload resilience.
"""

import os
import shutil
import tempfile
import unittest

from drive_rescue.contract import (
    CONTRACT_VERSION,
    SkillInput,
    FilePayload,
    SkillOutput,
    validate_input_contract,
    validate_output_contract,
)
from drive_rescue.registry import GLOBAL_REGISTRY, SkillRegistry
from drive_rescue.runtime.executor import GLOBAL_EXECUTOR


class TestSkillContractConformance(unittest.TestCase):
    """Verifies that all skills in the skills/ directory comply strictly with Contract v1."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="dr_conformance_")
        cls.input_dir = os.path.join(cls.test_dir, "input")
        cls.output_dir = os.path.join(cls.test_dir, "output")
        os.makedirs(cls.input_dir, exist_ok=True)
        os.makedirs(cls.output_dir, exist_ok=True)

        # Create sample test files
        cls.sample_files = {}

        # 1. Corrupted PNG (corrupt magic bytes)
        png_path = os.path.join(cls.input_dir, "damaged_photo.png.partial")
        with open(png_path, "wb") as f:
            f.write(b"XXXX\r\n\x1a\n" + b"\x00" * 100 + b"IEND\xaeB`\x82")
        cls.sample_files["png"] = png_path

        # 2. Corrupted PDF
        pdf_path = os.path.join(cls.input_dir, "invoice.pdf.partial")
        with open(pdf_path, "wb") as f:
            f.write(b"CORRUPT_HEADER\nobj 1 0\n<< /Type /Catalog >>\nendobj\n%%EOF")
        cls.sample_files["pdf"] = pdf_path

        # 3. Corrupted SQLite
        db_path = os.path.join(cls.input_dir, "database.sqlite.partial")
        with open(db_path, "wb") as f:
            f.write(b"BAD_SQLITE_MAGIC\x00" + b"\x00" * 200)
        cls.sample_files["sqlite"] = db_path

        # 4. Unknown Binary (starts with PNG signature but has .bin extension)
        unk_path = os.path.join(cls.input_dir, "mystery_blob.bin")
        with open(unk_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50)
        cls.sample_files["unknown"] = unk_path

        # 5. Incomplete bad sectors payload
        inc_path = os.path.join(cls.input_dir, "fragmented_doc.docx.partial")
        with open(inc_path, "wb") as f:
            f.write(b"PK\x03\x04" + b"UNREADABLE_BAD_SECTOR" * 10)
        cls.sample_files["incomplete"] = inc_path

        # 6. Suspicious script binary for malware scanning
        mal_path = os.path.join(cls.input_dir, "suspicious_loader.exe")
        with open(mal_path, "wb") as f:
            f.write(b"powershell -encodedcommand dGVzdA== powershell.exe bypass")
        cls.sample_files["suspicious"] = mal_path

        # Discover all skills in repository
        cls.registry = SkillRegistry()
        skills_base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
        cls.discovered_count = cls.registry.discover_directory(skills_base)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_all_10_skills_discovered(self):
        """Ensure all 10 decoupled skills are discovered with valid manifests."""
        self.assertGreaterEqual(self.discovered_count, 10)
        skills = self.registry.list_all_skills()
        skill_ids = [s["id"] for s in skills]

        expected_ids = [
            "repair-corrupted-files",
            "restore-backup-versions",
            "open-in-alternative-apps",
            "type-conversion-engine",
            "identify-unknown-file-types",
            "rebuild-incomplete-files",
            "raw-cluster-carving",
            "scan-for-malware-quarantine",
            "secure-archive-encryption",
            "smart-sorter-reorganizer",
        ]

        for exp_id in expected_ids:
            self.assertIn(exp_id, skill_ids, f"Expected skill '{exp_id}' not found in registry")

    def test_health_check_conformance(self):
        """Ensure every registered skill responds with a valid SkillHealth contract."""
        for skill_id in self.registry._catalog.keys():
            manifest = self.registry.get_skill_manifest(skill_id)
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest.contract_version, CONTRACT_VERSION)

            instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)
            self.assertIsNotNone(instance, f"Failed to instantiate runtime for skill '{skill_id}'")

            health = instance.health()
            self.assertEqual(health.status, "OK")
            self.assertEqual(health.skill_id, skill_id)
            self.assertEqual(health.contract_version, CONTRACT_VERSION)
            self.assertGreaterEqual(health.uptime_seconds, 0.0)

    def test_execute_repair_corrupted_files(self):
        """Test repair-corrupted-files skill reconstructing magic headers."""
        manifest = self.registry.get_skill_manifest("repair-corrupted-files")
        instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)

        payload = SkillInput(
            skill_id="repair-corrupted-files",
            files=[
                FilePayload(id=1, name="damaged_photo.png.partial", real_path=self.sample_files["png"], status="Partial"),
                FilePayload(id=2, name="invoice.pdf.partial", real_path=self.sample_files["pdf"], status="Partial"),
                FilePayload(id=3, name="database.sqlite.partial", real_path=self.sample_files["sqlite"], status="Partial"),
            ],
            destination_dir=os.path.join(self.output_dir, "repair_out"),
        )

        output = instance.validate_and_execute(payload)
        self.assertTrue(output.success)
        self.assertEqual(output.skill_id, "repair-corrupted-files")
        self.assertEqual(output.repaired_count, 3)
        self.assertEqual(len(output.processed_files), 3)

        # Verify fixed PNG header
        out_png = output.processed_files[0].output_path
        self.assertTrue(os.path.exists(out_png))
        with open(out_png, "rb") as f:
            header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n")

    def test_execute_identify_unknown_file_types(self):
        """Test identify-unknown-file-types heuristic signature detection."""
        manifest = self.registry.get_skill_manifest("identify-unknown-file-types")
        instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)

        payload = SkillInput(
            skill_id="identify-unknown-file-types",
            files=[
                FilePayload(id=4, name="mystery_blob.bin", real_path=self.sample_files["unknown"], status="Partial"),
            ],
            destination_dir=os.path.join(self.output_dir, "identify_out"),
        )

        output = instance.validate_and_execute(payload)
        self.assertTrue(output.success)
        self.assertEqual(len(output.processed_files), 1)
        self.assertTrue(output.processed_files[0].output_name.endswith(".png"))

    def test_execute_malware_quarantine(self):
        """Test scan-for-malware-quarantine skill detection & isolation."""
        manifest = self.registry.get_skill_manifest("scan-for-malware-quarantine")
        instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)

        payload = SkillInput(
            skill_id="scan-for-malware-quarantine",
            files=[
                FilePayload(id=5, name="suspicious_loader.exe", real_path=self.sample_files["suspicious"], status="Partial"),
            ],
            destination_dir=os.path.join(self.output_dir, "quarantine_out"),
        )

        output = instance.validate_and_execute(payload)
        self.assertTrue(output.success)
        self.assertEqual(output.quarantined_count, 1)
        self.assertEqual(output.processed_files[0].status, "QUARANTINED")

    def test_execute_smart_sorter(self):
        """Test smart-sorter-reorganizer directory hierarchy sorting."""
        manifest = self.registry.get_skill_manifest("smart-sorter-reorganizer")
        instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)

        payload = SkillInput(
            skill_id="smart-sorter-reorganizer",
            files=[
                FilePayload(id=6, name="invoice.pdf", real_path=self.sample_files["pdf"], status="Good"),
                FilePayload(id=7, name="damaged_photo.png", real_path=self.sample_files["png"], status="Good"),
            ],
            destination_dir=os.path.join(self.output_dir, "sorted_out"),
        )

        output = instance.validate_and_execute(payload)
        self.assertTrue(output.success)
        self.assertEqual(output.sorted_count, 2)
        self.assertIn("Repaired_Documents", output.processed_files[0].output_path)
        self.assertIn("Repaired_Media", output.processed_files[1].output_path)

    def test_invalid_input_schema_handling(self):
        """Ensure invalid inputs return structured errors without raising unhandled exceptions."""
        manifest = self.registry.get_skill_manifest("repair-corrupted-files")
        instance = GLOBAL_EXECUTOR._load_skill_instance(manifest)

        # Missing destination_dir and skill_id
        invalid_payload = SkillInput(
            skill_id="",
            files=[],
            destination_dir="",
        )

        output = instance.validate_and_execute(invalid_payload)
        self.assertFalse(output.success)
        self.assertIsNotNone(output.error)
        self.assertEqual(output.error.code, "INVALID_INPUT_SCHEMA")


if __name__ == "__main__":
    unittest.main()
