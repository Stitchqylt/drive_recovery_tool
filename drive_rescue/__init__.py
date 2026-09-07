"""drive_rescue package initializer

This package provides a stable package-level import surface while keeping the original top-level modules intact during an incremental refactor.
It imports existing top-level modules dynamically and exposes them as attributes so users can import either the old top-level modules or the new package API.
"""

from importlib import import_module

__version__ = "1.0.0"

# Import core modules dynamically from top-level module names and expose them
_core_modules = [
    "main",
    "cli",
    "gui",
    "diskio",
    "partitions",
    "ntfs",
    "engine",
    "recovery_map",
    "monitor",
    "multipass_scheduler",
    "carver",
    "health",
    "hash_verify",
    "report",
    "dashboard",
]

for _m in _core_modules:
    try:
        globals()[_m] = import_module(_m)
    except Exception:
        # If the top-level module is not present yet, skip — compat wrappers will still work
        globals()[_m] = None

# Expose convenient top-level names
try:
    _main_mod = globals().get("main")
    main = _main_mod.main if _main_mod and hasattr(_main_mod, "main") else None
except Exception:
    main = None

__all__ = ["main"] + [m for m in _core_modules]
