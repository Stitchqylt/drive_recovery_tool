"""
Drive Rescue
============
A Zero-Freeze, Non-Destructive Raw Physical Drive & File Recovery Engine.

Features:
- Pure Python standard library implementation (0 third-party pip dependencies)
- Non-freezing async sector reads with hardware-level timeouts
- NTFS Master File Table (MFT) parser and raw cluster reconstructor
- Signature-based deep raw file carving
- Live Dashboard interface & Recovery Skills Workspace
- Multi-pass scheduling with bad-sector zero-fill and non-destructive mapping

Usage:
    >>> import drive_rescue as dr
    >>> dr.run_dashboard()
    >>> # or programmatic disk scanning
    >>> engine = dr.RecoveryEngine(reader, dest_dir="recovered")
    >>> engine.run_recovery(files)
"""

__version__ = "1.0.0"
__author__ = "Drive Rescue Contributors"
__license__ = "MIT"

from main import main
from diskio import (
    RawDiskReader,
    SectorBuffer,
    is_admin,
    list_physical_drives,
    IS_WINDOWS,
    IS_MACOS,
    IS_LINUX,
)
from dashboard import list_all_storage_devices, run_dashboard
from engine import (
    RecoveryEngine,
    RecoveryStats,
)
from partitions import PartitionInfo, scan_partitions
from ntfs import NTFSVolume, read_all_mft_records
from carver import FileCarver, SIGNATURES
from recovery_map import RecoveryMapFile
from multipass_scheduler import MultiPassScheduler
from health import query_smart_health
from report import generate_audit_report_html
from cli import run_cli

__all__ = [
    "__version__", "__author__", "__license__",
    "main",
    "run_dashboard",
    "run_cli",
    "RecoveryEngine",
    "RecoveryStats",
    "RawDiskReader",
    "SectorBuffer",
    "PartitionInfo",
    "scan_partitions",
    "NTFSVolume",
    "read_all_mft_records",
    "FileCarver",
    "SIGNATURES",
    "RecoveryMapFile",
    "MultiPassScheduler",
    "query_smart_health",
    "generate_audit_report_html",
    "is_admin",
    "list_physical_drives",
    "list_all_storage_devices",
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
]
