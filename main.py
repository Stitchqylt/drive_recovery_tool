"""
main.py - Entry point for Non-Freezing NTFS Raw Drive File Recovery Tool

Coordinates paired CLI, GUI (Windows), and Web Studio (cross-platform) execution,
administrative elevation checks, and parameter passthrough.
"""

import sys
import os
import argparse
import platform

from diskio import is_admin, IS_WINDOWS, IS_MACOS, IS_LINUX


def parse_global_args():
    parser = argparse.ArgumentParser(description="Non-Freezing Raw NTFS Disk File Recovery Tool")
    parser.add_argument("--cli", action="store_true", help="Force Command-Line Interface mode")
    parser.add_argument("--gui", action="store_true", help="Force Graphical User Interface mode (Windows/Tkinter)")
    parser.add_argument("--web", action="store_true", help="Launch Web Studio dashboard (cross-platform, http://localhost:8080)")
    parser.add_argument("--drive", type=str, default=None, help="Physical drive number (e.g. 1) or disk image path")
    parser.add_argument("--partition", type=int, default=None, help="Partition index (1-based)")
    parser.add_argument("--dest", type=str, default="recovered_files", help="Destination recovery directory")
    parser.add_argument("--timeout", type=int, default=1000, help="Per-sector read timeout in milliseconds")
    parser.add_argument("--extensions", type=str, default=None, help="Comma-separated priority extensions (e.g. docx,xlsx,pdf)")
    parser.add_argument("--reverse", action="store_true", help="Read drive/files in reverse direction")
    parser.add_argument("--multi-pass", action="store_true", help="Enable 3-Phase Multi-Pass Recovery")
    parser.add_argument("--carve", action="store_true", help="Run signature-based raw file carver")
    parser.add_argument("--resume", action="store_true", help="Auto-resume from existing recovery.map")
    parser.add_argument("--include-system", action="store_true", help="Include NTFS system metafiles")
    parser.add_argument("--max-records", type=int, default=100000, help="Max MFT records to scan")
    return parser.parse_args()


def main():
    args = parse_global_args()

    # Determine execution mode
    if args.web:
        # Explicit web studio request
        from dashboard import run_dashboard, run_web_studio
        print(f"[*] Starting Web Studio on http://127.0.0.1:8080")
        run_dashboard(open_browser=True)
        return

    # If --cli flag is explicitly passed, or if headless / non-interactive terminal without --gui
    use_cli = args.cli or (not args.gui and len(sys.argv) > 1 and not args.gui)

    if use_cli and not args.gui:
        from cli import run_cli
        run_cli(args)
    else:
        # GUI mode requested
        if not IS_WINDOWS:
            print("[*] Tkinter GUI not available on this platform. Launching Web Studio instead...")
            print("[*] Use --web flag explicitly to avoid this message.")
            from dashboard import run_dashboard, run_web_studio
            run_dashboard(open_browser=True)
        else:
            try:
                from gui import run_gui
                run_gui(
                    initial_drive=args.drive,
                    initial_dest=args.dest,
                    initial_timeout=args.timeout,
                    initial_include_sys=args.include_system,
                    initial_multi_pass=args.multi_pass,
                    initial_carve=args.carve,
                )
            except Exception as e:
                print(f"[!] GUI could not be initialized ({e}). Falling back to CLI mode...")
                from cli import run_cli
                run_cli(args)


if __name__ == "__main__":
    main()
