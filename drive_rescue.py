# Package shim for backwards compatibility
try:
    from drive_rescue import main
except ImportError:
    try:
        import main as _main
        main = _main.main
    except ImportError:
        main = None

__all__ = ['main']
