"""
__main__.py - Entry point when invoked via `python -m drive_rescue` or `python -m drive_recovery`
"""

import sys
from main import main

if __name__ == "__main__":
    sys.exit(main() or 0)
