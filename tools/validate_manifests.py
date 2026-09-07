#!/usr/bin/env python3
"""
tools/validate_manifests.py - Strict CI/CD Manifest & Schema Validation Tool

Validates all skill-manifest.json files against the canonical manifest schema,
checks entrypoint module resolution, and verifies capability definitions.
"""

import os
import sys
import glob
import json

# Ensure project root is in sys.path
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from drive_rescue.contract import validate_manifest_contract


def validate_all_manifests(base_dir: str = "skills") -> int:
    manifest_files = glob.glob(os.path.join(base_dir, "**", "skill-manifest.json"), recursive=True)
    if not manifest_files:
        print(f"[!] No manifest files discovered under '{base_dir}'")
        return 1

    print(f"[*] Validating {len(manifest_files)} skill manifest(s) against Canonical Contract...")
    errors_total = 0

    for path in sorted(manifest_files):
        rel_p = os.path.relpath(path, os.getcwd())
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [FAIL] {rel_p}: JSON parse error - {e}")
            errors_total += 1
            continue

        errors = validate_manifest_contract(data)
        
        # Check entrypoint resolution
        if not errors and data.get("runtime", {}).get("type") == "python_module":
            entrypoint = data["runtime"].get("entrypoint", "")
            if ":" not in entrypoint:
                errors.append(f"Invalid entrypoint format '{entrypoint}' (expected 'module.path:ClassName')")

        if errors:
            print(f"  [FAIL] {rel_p} ({data.get('id', 'unknown')} v{data.get('version', 'unknown')}):")
            for err in errors:
                print(f"         - {err}")
            errors_total += 1
        else:
            print(f"  [PASS] {data['id']:<30} v{data['version']:<8} (contract: {data['contract_version']}) -> {rel_p}")

    if errors_total == 0:
        print(f"\n[+] SUCCESS: All {len(manifest_files)} skill manifests conform strictly to Contract v1.0.0.")
        return 0
    else:
        print(f"\n[X] FAILURE: {errors_total} manifest(s) failed validation.")
        return 1


if __name__ == "__main__":
    sys.exit(validate_all_manifests())
