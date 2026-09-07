"""
cli.py - Command Line Interface for Non-Freezing Raw Drive Recovery

Interactive & Batch CLI with industry-standard features:
S.M.A.R.T. pre-flight diagnostics, cryptographic SHA-256 integrity verification,
priority extension filtering, reverse reading, and audit certificate generation.
"""

import os
import sys
import time
import argparse
from typing import Optional, Dict, Any, List

from diskio import RawDiskReader, list_physical_drives, is_admin
from partitions import scan_partitions, PartitionInfo
from ntfs import NTFSVolume, read_all_mft_records
from engine import RecoveryEngine, RecoveryStats
from recovery_map import RecoveryMapFile
from multipass_scheduler import MultiPassScheduler
from health import query_smart_health


def print_banner():
    banner = r"""
========================================================================
     Drive Rescue Raw Drive Recovery Engine (Industry Pro)
========================================================================
 Direct Win32 Overlapped Direct-I/O | Bad Sector Timeout & Zero-Fill
 S.M.A.R.T. Telemetry | SHA-256 Hashing | Priority Filter | Reverse Read
========================================================================
"""
    print(banner)


def format_bytes(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def handle_skills_cli(argv: List[str]) -> int:
    """Handles skills CLI commands: list, test, rollout."""
    from drive_rescue.registry import GLOBAL_REGISTRY
    from drive_rescue.runtime.executor import GLOBAL_EXECUTOR

    skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
    GLOBAL_REGISTRY.discover_directory(skills_dir)

    subcmd = argv[0].lower() if argv else "list"

    if subcmd == "list":
        skills = GLOBAL_REGISTRY.list_all_skills()
        print("\n" + "=" * 76)
        print(" DRIVE RESCUE - DECOUPLED RECOVERY SKILLS CATALOG")
        print("=" * 76)
        print(f" {'ID':<30} {'VERSION':<10} {'WEIGHT':<8} {'STATUS'}")
        print("-" * 76)
        health_map = GLOBAL_EXECUTOR.check_all_health()
        for s in skills:
            s_id = s["id"]
            ver = s["version"]
            wt = f"{s['rollout']['weight']}%"
            h_stat = health_map.get(s_id, {}).get("status", "OK")
            print(f" {s_id:<30} {ver:<10} {wt:<8} {h_stat}")
            print(f"   -> {s['description']}")
            triggers_ext = s['triggers']['extensions'][:5] if s['triggers']['extensions'] else ['*']
            print(f"   -> Triggers: {s['triggers']['file_statuses']}, Exts: {triggers_ext}")
        print("=" * 76 + "\n")
        return 0

    elif subcmd == "test":
        if len(argv) < 2:
            print("[!] Usage: python3 cli.py skills test <skill_id>")
            return 1
        skill_id = argv[1]
        manifest = GLOBAL_REGISTRY.get_skill_manifest(skill_id)
        if not manifest:
            print(f"[X] Skill '{skill_id}' not found in registry.")
            return 1

        print(f"\n[*] Testing Health & Contract Conformance for skill '{skill_id}'...")
        health_map = GLOBAL_EXECUTOR.check_all_health()
        h = health_map.get(skill_id, {})
        print(f"    - Health Status: {h.get('status')}")
        print(f"    - Version: {manifest.version}")
        print(f"    - Circuit Breaker: {h.get('circuit_breaker_state', 'CLOSED')}")
        print(f"    - Entrypoint: {manifest.runtime.entrypoint}")
        print(f"    - Capabilities: {', '.join(manifest.capabilities)}")
        print("[+] Skill check complete.\n")
        return 0

    elif subcmd == "rollout":
        if len(argv) < 3:
            print("[!] Usage: python3 cli.py skills rollout <skill_id> --weight <pct>")
            return 1
        skill_id = argv[1]
        weight = 100
        for i, a in enumerate(argv):
            if a in ("--weight", "-w") and i + 1 < len(argv):
                try:
                    weight = int(argv[i + 1])
                except ValueError:
                    print("[X] Weight must be an integer (0-100)")
                    return 1
            elif a.isdigit() and i > 1:
                weight = int(a)

        manifest = GLOBAL_REGISTRY.get_skill_manifest(skill_id)
        if not manifest:
            print(f"[X] Skill '{skill_id}' not found.")
            return 1
        if GLOBAL_REGISTRY.update_rollout_weight(skill_id, manifest.version, weight):
            print(f"[+] Canary rollout weight for '{skill_id}' v{manifest.version} updated to {weight}%.")
            return 0
        else:
            print(f"[X] Failed to update rollout weight for '{skill_id}'.")
            return 1
    else:
        print(f"[X] Unknown skills subcommand '{subcmd}'. Available: list, test, rollout")
        return 1


def parse_args():
    if len(sys.argv) > 1 and sys.argv[1].lower() == "skills":
        sys.exit(handle_skills_cli(sys.argv[2:]))

    parser = argparse.ArgumentParser(description="Non-Freezing Raw NTFS Disk File Recovery Tool (Industry Pro)")
    parser.add_argument("--drive", type=str, help="Physical drive number (e.g. 1) or disk image path")
    parser.add_argument("--partition", type=int, default=None, help="Partition index (1-based)")
    parser.add_argument("--dest", type=str, default="recovered_files", help="Destination recovery directory")
    parser.add_argument("--timeout", type=int, default=1000, help="Per-sector read timeout in milliseconds (default: 1000)")
    parser.add_argument("--include-system", action="store_true", help="Include NTFS system metafiles ($MFT, etc.)")
    parser.add_argument("--extensions", type=str, default=None, help="Comma-separated priority extensions to recover (e.g. docx,xlsx,pdf,jpg,sql)")
    parser.add_argument("--reverse", action="store_true", help="Read drive/files in reverse direction (back-to-front)")
    parser.add_argument("--multi-pass", action="store_true", help="Enable 3-Phase Multi-Pass Recovery (Fast Sweep -> Files -> Scraping)")
    parser.add_argument("--carve", action="store_true", help="Run signature-based raw file carver for orphan files")
    parser.add_argument("--resume", action="store_true", help="Auto-resume from existing recovery.map in destination directory")
    parser.add_argument("--max-records", type=int, default=100000, help="Max MFT records to scan (default: 100000)")
    parser.add_argument("--gui", action="store_true", help="Launch GUI mode")
    return parser.parse_args()


def run_cli(args=None):
    if args is None:
        args = parse_args()

    print_banner()

    if not is_admin():
        print("[!] WARNING: Program is NOT running with Administrator privileges.")
        print("    Raw physical drive access (\\\\.\\PhysicalDriveX) requires Run as Administrator.\n")

    dest_dir = os.path.abspath(args.dest)
    mapfile_path = os.path.join(dest_dir, "recovery.map")

    # Check for existing session resume
    if os.path.exists(mapfile_path):
        print(f"[*] Found existing recovery session journal at:\n    {mapfile_path}")
        if not args.resume:
            ans = input("    Do you want to resume this previous session? (Y/n): ").strip().lower()
            if ans != "n":
                args.resume = True
        if args.resume:
            print("[+] Session resume ENABLED. Already-recovered sectors will be preserved.")

    # Select Drive
    selected_device = ""
    if args.drive:
        if args.drive.isdigit():
            selected_device = rf"\\.\PhysicalDrive{args.drive}"
        else:
            selected_device = args.drive
    else:
        print("Scanning physical drives on system...")
        drives = list_physical_drives()
        if not drives:
            print("[X] No physical drives detected.")
            return

        print("\nAvailable Drives:")
        for d in drives:
            print(f"  [{d['index']}] {d['name']} -> {d['device_path']}")

        print("  [F] Enter custom disk image file path")

        choice = input("\nSelect physical drive number (or 'F' for file): ").strip()
        if choice.upper() == "F":
            selected_device = input("Enter full path to disk image (.img/.raw): ").strip()
        elif choice.isdigit():
            selected_device = rf"\\.\PhysicalDrive{choice}"
        else:
            print("[X] Invalid selection.")
            return

    print(f"\n[*] Opening target device: {selected_device}")
    try:
        reader = RawDiskReader(selected_device, sector_size=512, default_timeout_ms=args.timeout)
    except Exception as e:
        print(f"[X] Error opening drive: {e}")
        return

    # S.M.A.R.T. Pre-Flight Health Check
    print("[*] Querying S.M.A.R.T. Hardware Health Telemetry...")
    smart_report = query_smart_health(selected_device, handle=getattr(reader, "handle", None))
    print(f"    - Hardware Health Verdict: {smart_report.health_verdict}")
    print(f"    - Reallocated Sectors: {smart_report.reallocated_sectors}")
    print(f"    - Pending Bad Sectors: {smart_report.pending_sectors}")
    print(f"    - Temperature: {smart_report.temperature_c}  degC")

    # Scan Partitions
    print("\n[*] Scanning partition tables (MBR/GPT)...")
    partitions = scan_partitions(reader)
    if not partitions:
        print("[X] No partitions or NTFS volumes detected on device.")
        reader.close()
        return

    print(f"\nDiscovered {len(partitions)} partition(s):")
    ntfs_partitions = []
    for idx, p in enumerate(partitions):
        ntfs_tag = "[NTFS Volume]" if p.is_ntfs else "[Non-NTFS / Unknown]"
        print(f"  [{idx + 1}] {p.name} | Start LBA: {p.start_lba:,} | Size: {p.size_gb:.2f} GB | {ntfs_tag}")
        if p.is_ntfs:
            ntfs_partitions.append((idx + 1, p))

    if args.partition and 1 <= args.partition <= len(partitions):
        target_part = partitions[args.partition - 1]
    elif not ntfs_partitions:
        print("\n[!] No NTFS partitions detected automatically.")
        part_idx = int(input("Enter partition number to force NTFS parse (1-based): ").strip()) - 1
        target_part = partitions[part_idx]
    elif len(ntfs_partitions) == 1:
        target_part = ntfs_partitions[0][1]
        print(f"\n[*] Automatically selected NTFS Partition: {target_part.name} @ LBA {target_part.start_lba:,}")
    else:
        choice = input(f"\nSelect partition number (1-{len(partitions)}): ").strip()
        part_idx = int(choice) - 1
        target_part = partitions[part_idx]

    # Parse NTFS VBR
    print(f"\n[*] Initializing NTFS Volume at LBA {target_part.start_lba:,}...")
    try:
        volume = NTFSVolume(reader, target_part.start_lba)
    except Exception as e:
        print(f"[X] Failed to parse NTFS Volume: {e}")
        reader.close()
        return

    # Parse MFT records
    print("\n[*] Reading Master File Table ($MFT)...")
    try:
        files = read_all_mft_records(
            volume,
            max_records=args.max_records,
            progress_callback=lambda count: print(f"    Scanned {count:,} MFT records...", end="\r", flush=True),
        )
        print(f"\n[+] Successfully parsed {len(files):,} active file records from $MFT.")
    except Exception as e:
        print(f"\n[X] Error reading MFT: {e}")
        reader.close()
        return

    target_exts = [e.strip() for e in args.extensions.split(",")] if args.extensions else None

    recoverable_files = {
        rec_id: f for rec_id, f in files.items()
        if not f.is_directory and f.name and (args.include_system or not f.name.startswith("$"))
    }
    if target_exts:
        recoverable_files = {
            rec_id: f for rec_id, f in recoverable_files.items()
            if "." in f.name and f.name.split(".")[-1].lower() in target_exts
        }
        print(f"[*] Priority Filter Applied: {len(recoverable_files):,} files matching extensions ({', '.join(target_exts)})")

    total_data_size = sum(f.file_size for f in recoverable_files.values())
    print(f"[*] Found {len(recoverable_files):,} target user files ({format_bytes(total_data_size)}).")
    print(f"[*] Destination Directory: {dest_dir}")
    print(f"[*] Per-Sector Read Timeout: {args.timeout} ms")
    if args.reverse:
        print("[*] Read Direction: REVERSE (Back-to-Front)")

    if not args.resume:
        proceed = input("\nStart file recovery now? (y/N): ").strip().lower()
        if proceed != "y":
            print("Aborted.")
            reader.close()
            return

    # Initialize Recovery Engine
    engine = RecoveryEngine(
        volume=volume,
        dest_dir=dest_dir,
        timeout_ms=args.timeout,
        include_system_files=args.include_system,
        target_extensions=target_exts,
        reverse_direction=args.reverse,
    )

    # Multi-Pass Phase 1
    if args.multi_pass:
        print("\n" + "=" * 70)
        print(" PHASE 1: FAST SWEEP & ADAPTIVE JUMP")
        print("=" * 70)
        scheduler = MultiPassScheduler(volume=volume, mapfile=engine.mapfile, dest_dir=dest_dir, timeout_ms=args.timeout)
        scheduler.run_phase1_fast_sweep(
            chunk_clusters=32,
            initial_jump_clusters=64,
            progress_callback=lambda cur, tot, msg: print(f"    Sweep: {cur:,}/{tot:,} clusters...", end="\r", flush=True),
        )
        print("\n[+] Phase 1 Fast Sweep completed.")

    # Phase 2: File Extraction
    print("\n" + "=" * 70)
    print(" PHASE 2: TARGETED FILE EXTRACTION (Press Ctrl+C to pause)")
    print("=" * 70)

    last_update_time = [0.0]

    def on_progress(stats: dict, current_file, outcome: str):
        now = time.time()
        if now - last_update_time[0] < 0.1 and stats["processed_files"] < stats["total_files"]:
            return
        last_update_time[0] = now

        pct = stats["percent_complete"]
        bar_len = 20
        filled_len = int(bar_len * pct / 100.0)
        bar = "=" * filled_len + "-" * (bar_len - filled_len)

        fname = (current_file.full_path or current_file.name)
        if len(fname) > 30:
            fname = "..." + fname[-27:]

        sys.stdout.write(
            f"\r[{bar}] {pct:5.1f}% | "
            f"Files: {stats['processed_files']}/{stats['total_files']} | "
            f"Good: {stats['good_sectors']:,} | "
            f"Bad: {stats['bad_sectors']:,} | "
            f"Cur: {fname:<30}"
        )
        sys.stdout.flush()

    try:
        final_stats = engine.run_recovery(recoverable_files, progress_callback=on_progress)
    except KeyboardInterrupt:
        print("\n\n[!] Pausing recovery session. Saving mapfile journal...")
        engine.cancel()
        final_stats = engine.stats

    # Phase 3: Scraping
    if args.multi_pass and not engine.stats.is_cancelled:
        print("\n\n" + "=" * 70)
        print(" PHASE 3: SCRAPING & TRIMMING BAD SECTOR BOUNDARIES")
        print("=" * 70)
        scheduler = MultiPassScheduler(volume=volume, mapfile=engine.mapfile, dest_dir=dest_dir, timeout_ms=args.timeout)
        scheduler.run_phase3_scraping(
            progress_callback=lambda cur, tot, msg: print(f"    Scraping: {cur:,}/{tot:,} sectors...", end="\r", flush=True),
        )
        print("\n[+] Phase 3 Scraping completed.")

    # Phase 4: Carving
    if args.carve and not engine.stats.is_cancelled:
        print("\n" + "=" * 70)
        print(" PHASE 4: SIGNATURE-BASED RAW FILE CARVER")
        print("=" * 70)
        carved = engine.carve_orphan_files(
            start_lba=target_part.start_lba,
            sector_count=min(volume.total_sectors, 500000),
            progress_callback=lambda cur, tot, cnt: print(f"    Carver: {cur:,}/{tot:,} sectors, carved {cnt} files...", end="\r", flush=True),
        )
        print(f"\n[+] Raw Carver extracted {len(carved)} orphan files.")

    print("\n" + "=" * 70)
    print(" RECOVERY SUMMARY & AUDIT")
    print("=" * 70)
    st = final_stats.to_dict() if isinstance(final_stats, RecoveryStats) else final_stats
    print(f"  Total Files Processed   : {st['processed_files']:,} / {st['total_files']:,}")
    print(f"  Fully Recovered (Good)  : {st['recovered_files']:,}")
    print(f"  Partially Recovered     : {st['partial_files']:,} (zero-filled bad sectors)")
    print(f"  Failed (Unreadable)     : {st['failed_files']:,}")
    print(f"  Good Sectors Read       : {st['good_sectors']:,}")
    print(f"  Bad / Timeout Sectors   : {st['bad_sectors']:,}")
    print(f"  Total Data Written      : {format_bytes(st['total_bytes_written'])}")
    print(f"  Total Elapsed Time      : {st['elapsed_seconds']:.1f} seconds")
    print(f"\n  Forensic Audit Report   : {engine.audit_report_path}")
    print(f"  Mapfile Journal         : {engine.mapfile_path}")
    print(f"  Manifest (with SHA-256) : {engine.manifest_path}")
    print(f"  Bad Sectors Log         : {engine.bad_sectors_path}")
    print("=" * 70)

    reader.close()


if __name__ == "__main__":
    run_cli()
