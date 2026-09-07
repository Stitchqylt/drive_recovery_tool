"""
tests/test_skill_registry.py - Unit & Conformance Tests for Dynamic Skill Registry & Canary Catalog
"""

import os
import unittest
from drive_rescue.registry import SkillRegistry, SkillManifest, SkillTriggers, SkillRolloutSpec


class TestSkillRegistry(unittest.TestCase):
    """Verifies manifest registration, discovery, trigger resolution, and canary weighting."""

    def setUp(self):
        self.registry = SkillRegistry()

    def test_manifest_registration_and_retrieval(self):
        m1 = SkillManifest(
            id="test-skill",
            name="Test Skill v1",
            version="1.0.0",
            description="First version",
            rollout=SkillRolloutSpec(weight=100),
        )
        self.assertTrue(self.registry.register_manifest(m1))
        retrieved = self.registry.get_skill_manifest("test-skill", version="1.0.0")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.version, "1.0.0")

    def test_dynamic_discovery(self):
        skills_base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
        count = self.registry.discover_directory(skills_base)
        self.assertGreaterEqual(count, 10)

        all_skills = self.registry.list_all_skills()
        self.assertGreaterEqual(len(all_skills), 10)

    def test_trigger_matching(self):
        m_media = SkillManifest(
            id="media-fixer",
            name="Media Fixer",
            version="1.0.0",
            description="Fixes media",
            triggers=SkillTriggers(
                file_statuses=["Partial"],
                extensions=[".mp4", ".mov", ".png"],
                mime_types=["video/", "image/"],
            ),
        )
        self.registry.register_manifest(m_media)

        # Match by status and extension
        matches = self.registry.find_matching_skills(file_status="Partial", filename="video.mp4", mime_type="video/mp4")
        self.assertTrue(any(m.id == "media-fixer" for m in matches))

        # Does not match good PDF
        matches_pdf = self.registry.find_matching_skills(file_status="Good", filename="doc.pdf", mime_type="application/pdf")
        self.assertFalse(any(m.id == "media-fixer" for m in matches_pdf))

    def test_canary_rollout_weight_update(self):
        m_v1 = SkillManifest(
            id="rollout-skill",
            name="Rollout Skill",
            version="1.0.0",
            description="v1",
            rollout=SkillRolloutSpec(weight=100),
        )
        m_v2 = SkillManifest(
            id="rollout-skill",
            name="Rollout Skill",
            version="2.0.0",
            description="v2",
            rollout=SkillRolloutSpec(weight=0),
        )
        self.registry.register_manifest(m_v1)
        self.registry.register_manifest(m_v2)

        # Initially 100% v1
        self.assertEqual(self.registry.get_skill_manifest("rollout-skill").version, "1.0.0")

        # Update v2 to 100% and v1 to 0%
        self.assertTrue(self.registry.update_rollout_weight("rollout-skill", "2.0.0", 100))
        self.assertTrue(self.registry.update_rollout_weight("rollout-skill", "1.0.0", 0))

        # Now should resolve to v2
        self.assertEqual(self.registry.get_skill_manifest("rollout-skill").version, "2.0.0")


if __name__ == "__main__":
    unittest.main()
