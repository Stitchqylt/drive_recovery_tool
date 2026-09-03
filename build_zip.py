"""
build_zip.py - Packages the entire Drive Recovery Tool into a ready-to-distribute Zip archive.
"""

import os
import zipfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ZIP_NAME = "DriveRecoveryTool_v1.0.zip"
ZIP_PATH = os.path.join(PROJECT_DIR, ZIP_NAME)

FILES_TO_INCLUDE = [
    "main.py",
    "raw_io.py",
    "disk_layout.py",
    "ntfs_parser.py",
    "recovery_engine.py",
    "mapfile.py",
    "watchdog.py",
    "multipass_scheduler.py",
    "file_carver.py",
    "smart_monitor.py",
    "hash_verifier.py",
    "audit_report.py",
    "cli.py",
    "gui.py",
    "web_studio.py",
    "studio.html",
    "RUN_GUI.bat",
    "RUN_WEB_STUDIO.bat",
    "RUN_CLI.bat",
    "BUILD_EXE.bat",
    "README.md",
    "tests/test_suite.py",
]


def create_zip():
    print(f"Creating distribution package: {ZIP_PATH}...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in FILES_TO_INCLUDE:
            full_path = os.path.join(PROJECT_DIR, rel_path)
            if os.path.exists(full_path):
                # Archive name under folder DriveRecoveryTool/
                arcname = os.path.join("DriveRecoveryTool", rel_path)
                zf.write(full_path, arcname)
                print(f"  + Added: {arcname}")
            else:
                print(f"  ! Warning: {rel_path} not found")

    size_kb = os.path.getsize(ZIP_PATH) / 1024.0
    print(f"\n[+] Successfully generated {ZIP_NAME} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    create_zip()
