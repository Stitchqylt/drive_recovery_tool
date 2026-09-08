"""
drive_rescue.registry.registry - Dynamic Skill Registry & Catalog

Discovers, registers, validates manifests, queries skills by trigger conditions,
and routes invocations with canary rollout traffic weighting and fallbacks.
"""

import os
import sys
import glob
import json
import time
import random
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

from drive_rescue.contract import CONTRACT_VERSION, SkillHealth


@dataclass
class SkillRuntimeSpec:
    """Defines how a skill is invoked (in-process python class, out-of-process HTTP/gRPC, WASM)."""
    type: str = "python_module"  # "python_module", "http", "wasm"
    entrypoint: str = ""
    endpoint: Optional[str] = None
    artifact_uri: Optional[str] = None
    timeout_ms: int = 30000

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillRolloutSpec:
    """Canary / Blue-Green rollout routing policy."""
    strategy: str = "canary"  # "canary", "blue_green", "direct"
    weight: int = 100  # 0 to 100 percentage of traffic
    fallback_version: Optional[str] = None
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillTriggers:
    """File condition & signature triggers that activate this skill."""
    file_statuses: List[str] = field(default_factory=lambda: ["Partial", "Failed"])
    extensions: List[str] = field(default_factory=list)
    mime_types: List[str] = field(default_factory=list)
    always_available: bool = False

    def matches_file(self, file_status: str, filename: str, mime_type: str = "") -> bool:
        if self.always_available:
            return True
        if self.file_statuses and file_status in self.file_statuses:
            return True
        ext = os.path.splitext(filename)[1].lower()
        if self.extensions and (ext in self.extensions or f".{ext}" in self.extensions):
            return True
        if self.mime_types and any(mime_type.startswith(m) for m in self.mime_types):
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillManifest:
    """Manifest specification declaring a recovery skill."""
    id: str
    name: str
    version: str
    description: str
    contract_version: str = CONTRACT_VERSION
    author: str = "Drive Rescue"
    triggers: SkillTriggers = field(default_factory=SkillTriggers)
    runtime: SkillRuntimeSpec = field(default_factory=SkillRuntimeSpec)
    rollout: SkillRolloutSpec = field(default_factory=SkillRolloutSpec)
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    manifest_path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Optional[str] = None) -> 'SkillManifest':
        triggers_data = data.get("triggers", {})
        triggers = SkillTriggers(**triggers_data) if isinstance(triggers_data, dict) else SkillTriggers()

        runtime_data = data.get("runtime", {})
        runtime = SkillRuntimeSpec(**runtime_data) if isinstance(runtime_data, dict) else SkillRuntimeSpec()

        rollout_data = data.get("rollout", {})
        rollout = SkillRolloutSpec(**rollout_data) if isinstance(rollout_data, dict) else SkillRolloutSpec()

        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", data.get("id", "Unnamed Skill"))),
            version=str(data.get("version", "1.0.0")),
            description=str(data.get("description", "")),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
            author=str(data.get("author", "Drive Rescue")),
            triggers=triggers,
            runtime=runtime,
            rollout=rollout,
            capabilities=list(data.get("capabilities", [])),
            metadata=dict(data.get("metadata", {})),
            manifest_path=path,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["triggers"] = self.triggers.to_dict()
        d["runtime"] = self.runtime.to_dict()
        d["rollout"] = self.rollout.to_dict()
        return d


class SkillRegistry:
    """
    Central catalog of all discovered recovery skills with versioning,
    canary routing, and trigger matching.
    """

    def __init__(self):
        # Map of skill_id -> { version_str -> SkillManifest }
        self._catalog: Dict[str, Dict[str, SkillManifest]] = {}
        # Cached skill runtime instances
        self._instances: Dict[str, Any] = {}
        # Health check cache
        self._health_cache: Dict[str, SkillHealth] = {}

    def register_manifest(self, manifest: SkillManifest) -> bool:
        """Registers a skill manifest into the catalog."""
        if not manifest.id or not manifest.version:
            return False
        if manifest.id not in self._catalog:
            self._catalog[manifest.id] = {}
        # If registering a higher version, adjust default rollout weights
        for v, old_m in self._catalog[manifest.id].items():
            if v != manifest.version and manifest.version > v:
                old_m.rollout.weight = 0
                manifest.rollout.fallback_version = v
        self._catalog[manifest.id][manifest.version] = manifest
        return True

    def discover_directory(self, base_dir: str) -> int:
        """Scans directory hierarchy for skill-manifest.json files."""
        found_count = 0
        if not os.path.exists(base_dir):
            return 0

        pattern = os.path.join(base_dir, "**", "skill-manifest.json")
        for manifest_path in glob.glob(pattern, recursive=True):
            try:
                # Add package root to sys.path for dynamic import resolution
                pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(manifest_path)))
                if pkg_root and pkg_root not in sys.path:
                    sys.path.insert(0, pkg_root)
                manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
                if manifest_dir and manifest_dir not in sys.path:
                    sys.path.insert(0, manifest_dir)

                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                manifest = SkillManifest.from_dict(data, path=manifest_path)
                if self.register_manifest(manifest):
                    found_count += 1
            except Exception as e:
                print(f"[-] Error loading manifest {manifest_path}: {e}")

        return found_count

    def get_skill_manifest(self, skill_id: str, version: Optional[str] = None) -> Optional[SkillManifest]:
        """
        Resolves a skill manifest. If version is not specified, selects the
        active version based on canary weights.
        """
        versions_map = self._catalog.get(skill_id)
        if not versions_map:
            return None

        if version and version in versions_map:
            return versions_map[version]

        # Single version available
        if len(versions_map) == 1:
            return list(versions_map.values())[0]

        # Canary traffic routing based on weight
        active_manifests = [m for m in versions_map.values() if m.rollout.is_active]
        if not active_manifests:
            return list(versions_map.values())[-1]

        total_weight = sum(m.rollout.weight for m in active_manifests)
        if total_weight <= 0:
            return active_manifests[0]

        roll = random.randint(1, total_weight)
        cumulative = 0
        for m in active_manifests:
            cumulative += m.rollout.weight
            if roll <= cumulative:
                return m

        return active_manifests[0]

    def list_all_skills(self) -> List[Dict[str, Any]]:
        """Returns catalog summary for UI and API consumers."""
        result = []
        for skill_id, versions_map in self._catalog.items():
            active_m = self.get_skill_manifest(skill_id)
            if active_m:
                skill_summary = active_m.to_dict()
                skill_summary["available_versions"] = list(versions_map.keys())
                result.append(skill_summary)
        return result

    def find_matching_skills(self, file_status: str, filename: str, mime_type: str = "") -> List[SkillManifest]:
        """Finds all registered skills that match a file's condition and type."""
        matches = []
        for skill_id in self._catalog:
            m = self.get_skill_manifest(skill_id)
            if m and m.triggers.matches_file(file_status, filename, mime_type):
                matches.append(m)
        return matches

    def update_rollout_weight(self, skill_id: str, version: str, weight: int) -> bool:
        """Dynamically adjusts canary traffic weight (0-100)."""
        versions_map = self._catalog.get(skill_id)
        if not versions_map or version not in versions_map:
            return False
        versions_map[version].rollout.weight = max(0, min(100, weight))
        return True


# Global registry singleton
GLOBAL_REGISTRY = SkillRegistry()
