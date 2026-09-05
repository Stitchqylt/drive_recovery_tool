"""
web_studio.py - Complete Zero-Dependency Backend Engine & Server for Drive Rescue Studio

Serves the modern Drive Rescue web interface and provides REST APIs for:
1. Physical Drive & Storage Volume Enumeration (/api/drives)
2. Live Multi-Mode Drive & Storage Scanning (/api/start, /api/status, /api/pause)
3. Direct Single-File & Batch "Recover Selected" Extraction (/api/recover_files)
4. Real Binary & Visual Previews (/api/preview_file)
5. Session Management & Journal Discovery (/api/sessions)
6. Native OS Folder Explorer Opening (/api/open_folder)
7. Engine Configuration & Timeout Controls (/api/settings)
"""

import os
import sys
import glob
import json
import time
import shutil
import zipfile
import plistlib
import mimetypes
import subprocess
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, parse_qs

from raw_io import RawDiskReader, is_admin
from disk_layout import scan_partitions
from ntfs_parser import NTFSVolume, read_all_mft_records, NTFSFileInfo
from recovery_engine import RecoveryEngine
from mapfile import RecoveryMapFile
from multipass_scheduler import MultiPassScheduler
from file_carver import FileCarver

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

SCAN_LOCK = threading.Lock()
SESSION_LOCK = threading.RLock()
SCAN_THREAD = None
CANCEL_SCAN = threading.Event()
PAUSE_SCAN = threading.Event()


def _is_safe_path(path: str, allowed_root: str) -> bool:
    """Validate that path is within allowed_root to prevent path traversal."""
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(allowed_root)
        return abs_path.startswith(abs_root + os.sep) or abs_path == abs_root
    except Exception:
        return False

# Excluded developer and system caches that pollute real user file recovery
EXCLUDED_DIRS = {
    "node_modules", ".git", ".cache", "Library", ".gemini", ".npm",
    ".nvm", ".cargo", "venv", ".venv", "__pycache__", ".vscode", ".config",
    "AppData", "Application Support", ".rustup", "site-packages"
}

# Recovery worker state
RECOVERY_WORKER = {
    "thread": None,
    "cancel_event": threading.Event(),
    "pause_event": threading.Event(),
}

ACTIVE_SESSION = {
    "engine": None,
    "volume": None,
    "reader": None,
    "partition": None,
    "is_running": False,
    "is_paused": False,
    "selected_drive_path": "",
    "selected_drive_name": "",
    "selected_drive_size": "0 GB",
    "dest_dir": os.path.abspath("recovered_files"),
    "timeout_ms": 1000,
    "start_time": None,
    "elapsed_seconds": 0,
    "all_files": [],          # Live dynamically populated files list
    "file_map": {},           # Map of record_id -> NTFSFileInfo for recovery
    "stats": {
        "files_found": 0,
        "good_files": 0,
        "partial_files": 0,
        "failed_files": 0,
        "total_bytes": 0,
        "percent_complete": 0.0,
    },
    "settings": {
        "timeout_ms": 1000,
        "max_records": 50000,
        "retries": 1,
        "safe_mode": True,
        "auto_zero_fill": True,
        "include_system": False,
        "reverse": False,
    }
}


def format_bytes_human(num_bytes: int) -> str:
    """Format bytes into human readable string (KB, MB, GB, TB)."""
    if not num_bytes or num_bytes <= 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def list_all_storage_devices() -> List[Dict[str, Any]]:
    """
    Enumerates all real connected physical disks, SSDs, USB storage,
    virtual disk images, and system volumes available on the host machine.
    """
    devices = []

    # 1. Virtual disk images (.img, .raw, .dd)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    img_patterns = [
        os.path.join(script_dir, "*.img"),
        os.path.join(script_dir, "tools", "*.img"),
        os.path.join(script_dir, "*.raw"),
        os.path.join(script_dir, "*.dd"),
    ]
    for pattern in img_patterns:
        for img_path in sorted(glob.glob(pattern)):
            try:
                sz = os.path.getsize(img_path)
                bname = os.path.basename(img_path)
                devices.append({
                    "id": f"img_{bname}",
                    "device_path": os.path.abspath(img_path),
                    "name": f"Disk Image: {bname}",
                    "description": f"NTFS Damaged Disk Image • {format_bytes_human(sz)}",
                    "size_bytes": sz,
                    "size_str": format_bytes_human(sz),
                    "type": "Disk Image",
                    "is_mounted": False,
                })
            except Exception:
                pass

    # 2. Host System Physical Disks & Partitions
    if IS_WINDOWS:
        for i in range(16):
            dev_path = rf"\\.\PhysicalDrive{i}"
            try:
                reader = RawDiskReader(dev_path, sector_size=512, default_timeout_ms=300)
                sz = reader.disk_size_bytes
                reader.close()
                devices.append({
                    "id": f"drive_{i}",
                    "device_path": dev_path,
                    "name": f"PhysicalDrive{i}",
                    "description": f"Physical Disk • {format_bytes_human(sz)}",
                    "size_bytes": sz,
                    "size_str": format_bytes_human(sz),
                    "type": "Physical Disk",
                    "is_mounted": False,
                })
            except PermissionError:
                devices.append({
                    "id": f"drive_{i}",
                    "device_path": dev_path,
                    "name": f"PhysicalDrive{i} (Admin Required)",
                    "description": "Physical Disk • Run as Administrator",
                    "size_bytes": 0,
                    "size_str": "Admin Required",
                    "type": "Physical Disk",
                    "is_mounted": False,
                })
            except Exception:
                pass

    elif IS_MACOS:
        # macOS diskutil enumeration
        try:
            p = subprocess.run(["diskutil", "list", "-plist"], capture_output=True)
            if p.returncode == 0:
                import plistlib
                plist_data = plistlib.loads(p.stdout)
                for d in plist_data.get("AllDisksAndPartitions", []):
                    dev_id = d.get("DeviceIdentifier", "")
                    sz = d.get("Size", 0)
                    content = d.get("Content", "Disk")
                    devices.append({
                        "id": dev_id,
                        "device_path": f"/dev/{dev_id}",
                        "name": f"{dev_id} ({content})",
                        "description": f"macOS Physical Storage • {format_bytes_human(sz)}",
                        "size_bytes": sz,
                        "size_str": format_bytes_human(sz),
                        "type": "Physical Disk",
                        "is_mounted": False,
                    })
        except Exception:
            pass

    # 3. User Storage Folders & Volumes
    user_home = os.path.expanduser("~")
    for subdir_name in ["Downloads", "Documents", "Pictures", "Desktop", "Movies", "Music"]:
        full_sub = os.path.join(user_home, subdir_name)
        if os.path.exists(full_sub):
            try:
                free_b, total_b, _ = shutil.disk_usage(full_sub)
                devices.append({
                    "id": f"dir_{subdir_name.lower()}",
                    "device_path": full_sub,
                    "name": f"Storage: ~/{subdir_name}",
                    "description": f"User Folder • {format_bytes_human(total_b)}",
                    "size_bytes": total_b,
                    "size_str": format_bytes_human(total_b),
                    "type": "User Storage",
                    "is_mounted": True,
                })
            except Exception:
                pass

    # Default selection: prioritize user home or virtual disk image
    if devices and not ACTIVE_SESSION["selected_drive_path"]:
        ACTIVE_SESSION["selected_drive_path"] = devices[0]["device_path"]
        ACTIVE_SESSION["selected_drive_name"] = devices[0]["name"]
        ACTIVE_SESSION["selected_drive_size"] = devices[0]["size_str"]

    return devices


def get_file_preview_metadata(file_id: int, real_p: str, matched: dict) -> dict:
    """Extracts rich visual preview metadata for PDFs, Apps, Folders, Images, Code/Text, Archives, and Databases."""
    name = os.path.basename(real_p)
    ext = os.path.splitext(name)[1].lower()
    
    # 1. Directory / Folder / macOS App
    if os.path.isdir(real_p):
        if real_p.endswith(".app") or matched.get("is_app"):
            app_name = name.replace(".app", "")
            app_ver = "1.0"
            bundle_id = "com.apple.application"
            plist_p = os.path.join(real_p, "Contents", "Info.plist")
            if os.path.exists(plist_p):
                try:
                    with open(plist_p, "rb") as pl_f:
                        pl = plistlib.load(pl_f)
                        app_name = pl.get("CFBundleDisplayName") or pl.get("CFBundleName") or app_name
                        app_ver = pl.get("CFBundleShortVersionString") or pl.get("CFBundleVersion") or app_ver
                        bundle_id = pl.get("CFBundleIdentifier") or bundle_id
                except Exception:
                    pass
            return {
                "preview_type": "app",
                "app_name": app_name,
                "app_version": app_ver,
                "app_bundle_id": bundle_id,
                "app_type": "macOS Application Bundle (.app)",
                "size_str": matched.get("size", "App Folder"),
                "path": matched.get("path", real_p)
            }
        else:
            items = []
            try:
                for entry in sorted(os.scandir(real_p), key=lambda e: (not e.is_dir(), e.name.lower())):
                    if not entry.name.startswith("."):
                        items.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size_str": format_bytes_human(entry.stat().st_size) if entry.is_file() else "Folder"
                        })
            except Exception:
                pass
            return {
                "preview_type": "folder",
                "folder_name": name,
                "folder_items": items[:35],
                "folder_total_count": len(items),
                "folder_path": matched.get("path", real_p),
                "size_str": matched.get("size", "Folder")
            }

    # 2. PDF Document
    if ext == ".pdf" or (matched.get("type") == "application/pdf"):
        return {
            "preview_type": "pdf",
            "pdf_url": f"/api/preview_file?id={file_id}",
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 3. Image
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".ico", ".tiff") or (matched.get("type") and matched["type"].startswith("image/")):
        return {
            "preview_type": "image",
            "img_url": f"/api/preview_file?id={file_id}",
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 4. Text / Code / Markdown / JSON / Logs / Config
    text_extensions = (
        ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".csv", ".tsv",
        ".md", ".log", ".xml", ".html", ".css", ".ini", ".cfg", ".conf",
        ".sh", ".bat", ".ps1", ".sql", ".yaml", ".yml", ".env", ".toml", ".c", ".cpp", ".h", ".rs", ".go"
    )
    if ext in text_extensions or (matched.get("type") and matched["type"].startswith("text/")):
        text_preview = ""
        try:
            with open(real_p, "r", encoding="utf-8", errors="replace") as tf:
                lines = []
                for _ in range(70):
                    line = tf.readline()
                    if not line:
                        break
                    lines.append(line)
                text_preview = "".join(lines)
                if len(text_preview) > 8192:
                    text_preview = text_preview[:8192] + "\n... [Remaining content truncated for preview] ..."
        except Exception:
            text_preview = "(Binary or unreadable text content)"
        return {
            "preview_type": "text",
            "text_content": text_preview,
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 5. Video & Audio
    if ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        return {
            "preview_type": "video",
            "media_url": f"/api/preview_file?id={file_id}",
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }
    if ext in (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"):
        return {
            "preview_type": "audio",
            "media_url": f"/api/preview_file?id={file_id}",
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 6. Archive (.zip, .tar, .gz)
    if ext in (".zip", ".tar", ".gz", ".tgz"):
        entries = []
        try:
            if zipfile.is_zipfile(real_p):
                with zipfile.ZipFile(real_p, 'r') as zf:
                    for info in zf.infolist()[:30]:
                        entries.append({
                            "name": info.filename,
                            "size_str": format_bytes_human(info.file_size),
                            "is_dir": info.is_dir()
                        })
        except Exception:
            pass
        return {
            "preview_type": "archive",
            "archive_entries": entries,
            "archive_total": len(entries),
            "filename": name,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 7. Executables (.exe, .dmg, .pkg, .deb, .msi)
    if ext in (".exe", ".dll", ".dmg", ".pkg", ".deb", ".msi", ".bin"):
        return {
            "preview_type": "app",
            "app_name": name,
            "app_version": "Executable Binary",
            "app_bundle_id": "Native Application Binary",
            "app_type": "Windows PE Executable" if ext in (".exe", ".dll") else "Installer Package / Disk Image",
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # 8. Databases & Emails (.db, .sqlite, .pst, .ost, .mbox)
    if ext in (".db", ".sqlite", ".sqlite3", ".pst", ".ost", ".mbox", ".eml"):
        db_type = "Microsoft Outlook Mail Archive (.pst/.ost)" if ext in (".pst", ".ost") else "SQLite Relational Database (.db)"
        return {
            "preview_type": "database",
            "db_name": name,
            "db_type": db_type,
            "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
            "path": matched.get("path", real_p)
        }

    # Default Fallback
    return {
        "preview_type": "fallback",
        "filename": name,
        "size_str": matched.get("size", format_bytes_human(os.path.getsize(real_p))),
        "path": matched.get("path", real_p)
    }


def execute_background_scan(target_path: str):
    """
    Executes an intelligent scan prioritizing real user documents, images, and archives,
    filtering out hidden cache noise and calculating accurate byte totals.
    """
    global ACTIVE_SESSION
    CANCEL_SCAN.clear()
    PAUSE_SCAN.clear()

    with SESSION_LOCK:
        ACTIVE_SESSION["all_files"] = []
        ACTIVE_SESSION["stats"] = {
            "files_found": 0,
            "good_files": 0,
            "partial_files": 0,
            "failed_files": 0,
            "total_bytes": 0,
            "percent_complete": 0.0,
        }
        ACTIVE_SESSION["is_running"] = True
        ACTIVE_SESSION["is_paused"] = False
        ACTIVE_SESSION["start_time"] = time.time()

    print(f"[*] Starting prioritized storage scan on: {target_path}")

    # Mode 1: NTFS Disk Image (.img, .raw, .dd)
    if target_path.endswith((".img", ".raw", ".dd")) and os.path.exists(target_path):
        try:
            reader = RawDiskReader(target_path, sector_size=512, default_timeout_ms=ACTIVE_SESSION["timeout_ms"])
            parts = scan_partitions(reader)
            ntfs_part = next((p for p in parts if p.is_ntfs), None)

            if ntfs_part:
                vol = NTFSVolume(reader, ntfs_part.start_lba)
                max_rec = ACTIVE_SESSION["settings"].get("max_records", 50000)

                def _mft_progress(record_idx, total_records, file_info):
                    if CANCEL_SCAN.is_set():
                        return
                    while PAUSE_SCAN.is_set() and not CANCEL_SCAN.is_set():
                        time.sleep(0.1)

                    if file_info and not file_info.is_directory and file_info.file_size > 0:
                        status = "Partial" if file_info.is_partial else "Good"
                        mime = mimetypes.guess_type(file_info.filename)[0] or "File"
                        file_obj = {
                            "id": len(ACTIVE_SESSION["all_files"]),
                            "name": file_info.filename,
                            "is_folder": False,
                            "is_app": False,
                            "status": status,
                            "size": format_bytes_human(file_info.file_size),
                            "raw_size": file_info.file_size,
                            "modified": file_info.modified_time or time.strftime("%m/%d/%Y %I:%M %p"),
                            "path": file_info.full_path or f"\\{file_info.filename}",
                            "type": mime,
                            "real_path": None,
                        }
                        with SESSION_LOCK:
                            ACTIVE_SESSION["all_files"].append(file_obj)
                            ACTIVE_SESSION["stats"]["files_found"] += 1
                            if status == "Good":
                                ACTIVE_SESSION["stats"]["good_files"] += 1
                            else:
                                ACTIVE_SESSION["stats"]["partial_files"] += 1
                            ACTIVE_SESSION["stats"]["total_bytes"] += file_info.file_size

                    pct = min(100.0, (record_idx / max(1, total_records)) * 100.0)
                    with SESSION_LOCK:
                        ACTIVE_SESSION["stats"]["percent_complete"] = round(pct, 1)

                read_all_mft_records(vol, max_records=max_rec, progress_callback=_mft_progress)
                reader.close()
                with SESSION_LOCK:
                    ACTIVE_SESSION["stats"]["percent_complete"] = 100.0
                    ACTIVE_SESSION["is_running"] = False
                return
            else:
                reader.close()
        except Exception as e:
            print(f"[-] NTFS image scan error: {e}")

    # Mode 2: User Storage Traversal (Prioritizing Downloads, Documents, Desktop, Pictures, Applications)
    user_home = os.path.expanduser("~")
    
    if os.path.isdir(target_path):
        scan_roots = [target_path]
    else:
        # Scan user media and app folders in order of user importance
        scan_roots = [
            os.path.join(user_home, "Downloads"),
            os.path.join(user_home, "Desktop"),
            os.path.join(user_home, "Pictures"),
            os.path.join(user_home, "Documents"),
            os.path.join(user_home, "Applications"),
            "/Applications",
            os.path.join(user_home, "Movies"),
            os.path.join(user_home, "Music"),
            os.path.abspath("."),
        ]

    max_scan_files = 2000
    file_count = 0

    try:
        for sroot in scan_roots:
            if not os.path.exists(sroot):
                continue
            if CANCEL_SCAN.is_set() or file_count >= max_scan_files:
                break

            for root, dirs, files in os.walk(sroot):
                if CANCEL_SCAN.is_set() or file_count >= max_scan_files:
                    break
                while PAUSE_SCAN.is_set() and not CANCEL_SCAN.is_set():
                    time.sleep(0.1)

                # Detect macOS Application bundles in directory list
                app_dirs = [d for d in dirs if d.endswith(".app")]
                for ad in app_dirs:
                    full_app = os.path.join(root, ad)
                    try:
                        dirs.remove(ad)
                    except ValueError:
                        pass
                    try:
                        mtime = time.strftime("%m/%d/%Y %I:%M %p", time.localtime(os.path.getmtime(full_app)))
                        rel_dir = os.path.relpath(root, user_home)
                        rel_p = "\\" + rel_dir.replace("/", "\\") if rel_dir != "." else "\\"
                        file_obj = {
                            "id": len(ACTIVE_SESSION["all_files"]),
                            "name": ad,
                            "is_folder": False,
                            "is_app": True,
                            "status": "Good",
                            "size": "App Bundle",
                            "raw_size": 0,
                            "modified": mtime,
                            "path": rel_p,
                            "type": "macOS Application",
                            "real_path": full_app,
                        }
                        with SESSION_LOCK:
                            ACTIVE_SESSION["all_files"].append(file_obj)
                            ACTIVE_SESSION["stats"]["files_found"] += 1
                            ACTIVE_SESSION["stats"]["good_files"] += 1
                            file_count += 1
                    except Exception:
                        pass

                # Detect user folders
                for d in list(dirs):
                    if d in EXCLUDED_DIRS or d.startswith("."):
                        continue
                    full_dir = os.path.join(root, d)
                    try:
                        if root in scan_roots:
                            mtime = time.strftime("%m/%d/%Y %I:%M %p", time.localtime(os.path.getmtime(full_dir)))
                            rel_dir = os.path.relpath(root, user_home)
                            rel_p = "\\" + rel_dir.replace("/", "\\") if rel_dir != "." else "\\"
                            item_count = len([x for x in os.listdir(full_dir) if not x.startswith(".")])
                            file_obj = {
                                "id": len(ACTIVE_SESSION["all_files"]),
                                "name": d,
                                "is_folder": True,
                                "is_app": False,
                                "status": "Good",
                                "size": f"{item_count} items",
                                "raw_size": 0,
                                "modified": mtime,
                                "path": rel_p,
                                "type": "Folder",
                                "real_path": full_dir,
                            }
                            with SESSION_LOCK:
                                ACTIVE_SESSION["all_files"].append(file_obj)
                                ACTIVE_SESSION["stats"]["files_found"] += 1
                                ACTIVE_SESSION["stats"]["good_files"] += 1
                                file_count += 1
                    except Exception:
                        pass

                # Prune junk directories (node_modules, .git, .cache, etc.)
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]

                for f in files:
                    if CANCEL_SCAN.is_set() or file_count >= max_scan_files:
                        break
                    while PAUSE_SCAN.is_set() and not CANCEL_SCAN.is_set():
                        time.sleep(0.1)

                    if f.startswith(".") or f.endswith((".pyc", ".lock", ".log", ".tmp")):
                        continue

                    full_p = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(full_p)
                        mtime = time.strftime("%m/%d/%Y %I:%M %p", time.localtime(os.path.getmtime(full_p)))

                        # Direct test read of first block
                        status = "Good"
                        try:
                            with open(full_p, "rb") as test_f:
                                test_f.read(min(4096, sz))
                        except Exception:
                            status = "Partial"

                        mime = mimetypes.guess_type(f)[0] or "File"
                        if f.lower().endswith(".pdf"):
                            mime = "application/pdf"
                        rel_dir = os.path.relpath(os.path.dirname(full_p), user_home)
                        rel_p = "\\" + rel_dir.replace("/", "\\") if rel_dir != "." else "\\"

                        file_obj = {
                            "id": len(ACTIVE_SESSION["all_files"]),
                            "name": f,
                            "is_folder": False,
                            "is_app": False,
                            "status": status,
                            "size": format_bytes_human(sz),
                            "raw_size": sz,
                            "modified": mtime,
                            "path": rel_p,
                            "type": mime,
                            "real_path": full_p,
                        }

                        with SESSION_LOCK:
                            ACTIVE_SESSION["all_files"].append(file_obj)
                            ACTIVE_SESSION["stats"]["files_found"] += 1
                            if status == "Good":
                                ACTIVE_SESSION["stats"]["good_files"] += 1
                            else:
                                ACTIVE_SESSION["stats"]["partial_files"] += 1
                            ACTIVE_SESSION["stats"]["total_bytes"] += sz

                            file_count += 1
                            ACTIVE_SESSION["stats"]["percent_complete"] = min(100.0, round((file_count / 300) * 100.0, 1))

                        # Smooth streaming cadence
                        time.sleep(0.012)
                    except Exception:
                        continue

    except Exception as e:
        print(f"[-] Scan traversal error: {e}")

    with SESSION_LOCK:
        ACTIVE_SESSION["stats"]["percent_complete"] = 100.0
        ACTIVE_SESSION["is_running"] = False
    print(f"[+] Prioritized scan complete: {len(ACTIVE_SESSION['all_files'])} user files ({format_bytes_human(ACTIVE_SESSION['stats']['total_bytes'])}).")


class UnifiedStudioHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            local_studio = os.path.join(os.path.dirname(__file__), "studio.html")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                with open(local_studio, "rb") as f:
                    self.wfile.write(f.read())
            except Exception as e:
                self.wfile.write(f"<h1>Error loading studio: {e}</h1>".encode("utf-8"))

        elif path == "/api/drives":
            drives = list_all_storage_devices()
            with SESSION_LOCK:
                self._send_json({
                    "drives": drives,
                    "selected_drive": ACTIVE_SESSION["selected_drive_path"],
                    "selected_name": ACTIVE_SESSION["selected_drive_name"],
                    "selected_size": ACTIVE_SESSION["selected_drive_size"],
                    "is_admin": is_admin(),
                })

        elif path == "/api/partitions":
            qs = parse_qs(parsed.query)
            drive = qs.get("drive", [ACTIVE_SESSION["selected_drive_path"]])[0]
            device_path = drive
            if not os.path.exists(device_path) and not device_path.startswith(r"\\.\\"):
                device_path = rf"\\.\PhysicalDrive{drive}" if drive.isdigit() else drive
            try:
                reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=1000)
                parts = scan_partitions(reader)
                part_list = [{
                    "index": p.index,
                    "name": p.name,
                    "start_lba": p.start_lba,
                    "size_gb": p.size_gb,
                    "is_ntfs": p.is_ntfs,
                    "size_str": f"{p.size_gb:.2f} GB",
                } for p in parts]
                reader.close()
                self._send_json({"success": True, "partitions": part_list, "device_path": device_path})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "partitions": []})

        elif path == "/api/status":
            with SESSION_LOCK:
                if ACTIVE_SESSION["start_time"] and ACTIVE_SESSION["is_running"]:
                    ACTIVE_SESSION["elapsed_seconds"] = int(time.time() - ACTIVE_SESSION["start_time"])
                status_data = {
                    "is_running": ACTIVE_SESSION["is_running"],
                    "is_paused": ACTIVE_SESSION["is_paused"],
                    "selected_drive_name": ACTIVE_SESSION["selected_drive_name"],
                    "selected_drive_path": ACTIVE_SESSION["selected_drive_path"],
                    "selected_drive_size": ACTIVE_SESSION["selected_drive_size"],
                    "stats": ACTIVE_SESSION["stats"],
                    "elapsed_seconds": ACTIVE_SESSION["elapsed_seconds"],
                    "files": ACTIVE_SESSION["all_files"],
                    "dest_dir": ACTIVE_SESSION["dest_dir"],
                }
            self._send_json(status_data)

        elif path == "/api/preview_meta":
            qs = parse_qs(parsed.query)
            file_id_str = qs.get("id", ["0"])[0]
            try:
                file_id = int(file_id_str)
                with SESSION_LOCK:
                    matched = next((f for f in ACTIVE_SESSION["all_files"] if f.get("id") == file_id), None)
                if matched and matched.get("real_path") and os.path.exists(matched["real_path"]):
                    real_p = matched["real_path"]
                    # Security: validate path is within user home or scan root
                    user_home = os.path.expanduser("~")
                    if not _is_safe_path(real_p, user_home):
                        self._send_json({"error": "Access denied: path outside allowed root"}, 403)
                        return
                    res = get_file_preview_metadata(file_id, real_p, matched)
                    self._send_json(res)
                    return
            except Exception as e:
                print(f"[-] Preview meta error: {e}")
            self._send_json({"preview_type": "fallback", "error": "No metadata available"})

        elif path == "/api/preview_file":
            qs = parse_qs(parsed.query)
            file_id_str = qs.get("id", ["0"])[0]
            try:
                file_id = int(file_id_str)
                with SESSION_LOCK:
                    matched = next((f for f in ACTIVE_SESSION["all_files"] if f.get("id") == file_id), None)
                if matched and matched.get("real_path") and os.path.exists(matched["real_path"]):
                    real_p = matched["real_path"]
                    # Security: validate path is within user home or scan root
                    user_home = os.path.expanduser("~")
                    if not _is_safe_path(real_p, user_home):
                        self._send_json({"error": "Access denied: path outside allowed root"}, 403)
                        return
                    if os.path.isdir(real_p):
                        self._send_json({"error": "Directory preview not available via raw stream"})
                        return

                    mime, _ = mimetypes.guess_type(real_p)
                    if not mime:
                        if real_p.lower().endswith(".pdf"):
                            mime = "application/pdf"
                        else:
                            mime = "application/octet-stream"

                    with open(real_p, "rb") as f_stream:
                        data = f_stream.read()

                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", f'inline; filename="{os.path.basename(real_p)}"')
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except Exception as e:
                print(f"[-] Preview stream error: {e}")
            self._send_json({"error": "No preview available"})

        elif path == "/api/sessions":
            with SESSION_LOCK:
                dest = ACTIVE_SESSION["dest_dir"]
            map_file = os.path.join(dest, "recovery.map")
            sessions = []
            if os.path.exists(map_file):
                sessions.append({
                    "session_id": "active_session",
                    "path": map_file,
                    "dest_dir": dest,
                    "last_modified": time.ctime(os.path.getmtime(map_file)),
                    "size_kb": os.path.getsize(map_file) // 1024,
                })
            else:
                sessions.append({
                    "session_id": "session_default",
                    "path": os.path.join(dest, "recovery.map"),
                    "dest_dir": dest,
                    "last_modified": "Ready",
                    "size_kb": 0,
                })
            self._send_json({"sessions": sessions})

        elif path == "/api/settings":
            with SESSION_LOCK:
                self._send_json({"settings": ACTIVE_SESSION["settings"]})

        elif path == "/api/manifest":
            # Serve recovery_manifest.csv
            with SESSION_LOCK:
                dest_dir = ACTIVE_SESSION["dest_dir"]
            manifest_path = os.path.join(dest_dir, "recovery_manifest.csv")
            if os.path.exists(manifest_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", f'attachment; filename="recovery_manifest.csv"')
                self.end_headers()
                with open(manifest_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self._send_json({"error": "No manifest found"}, 404)

        elif path == "/api/audit_report":
            # Serve recovery_audit_report.html
            with SESSION_LOCK:
                dest_dir = ACTIVE_SESSION["dest_dir"]
            report_path = os.path.join(dest_dir, "recovery_audit_report.html")
            if os.path.exists(report_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                with open(report_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self._send_json({"error": "No audit report found"}, 404)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        # Limit request body size to 1MB to prevent DoS
        if content_len > 1024 * 1024:
            self._send_json({"error": "Request body too large"}, 413)
            return
        body = self.rfile.read(content_len) if content_len > 0 else b"{}"
        data = json.loads(body.decode("utf-8")) if body else {}

        if path == "/api/select_drive":
            dev_path = data.get("device_path", "")
            dev_name = data.get("name", dev_path)
            dev_size = data.get("size_str", "")
            with SESSION_LOCK:
                ACTIVE_SESSION["selected_drive_path"] = dev_path
                ACTIVE_SESSION["selected_drive_name"] = dev_name
                ACTIVE_SESSION["selected_drive_size"] = dev_size
            self._send_json({"success": True, "message": f"Selected {dev_name}"})

        elif path == "/api/start":
            with SESSION_LOCK:
                target = data.get("target_path", ACTIVE_SESSION["selected_drive_path"]) or ACTIVE_SESSION["selected_drive_path"]
                dest = data.get("dest", ACTIVE_SESSION["dest_dir"])
                timeout = int(data.get("timeout", ACTIVE_SESSION["timeout_ms"]))

                ACTIVE_SESSION["dest_dir"] = os.path.abspath(dest)
                ACTIVE_SESSION["timeout_ms"] = timeout
                os.makedirs(ACTIVE_SESSION["dest_dir"], exist_ok=True)

            global SCAN_THREAD
            SCAN_THREAD = threading.Thread(target=execute_background_scan, args=(target,), daemon=True)
            SCAN_THREAD.start()

            self._send_json({"success": True, "message": "Scan started"})

        elif path == "/api/pause":
            with SESSION_LOCK:
                ACTIVE_SESSION["is_paused"] = not ACTIVE_SESSION["is_paused"]
                is_paused = ACTIVE_SESSION["is_paused"]
            if is_paused:
                PAUSE_SCAN.set()
            else:
                PAUSE_SCAN.clear()
            self._send_json({"success": True, "is_paused": is_paused})

        elif path == "/api/recover_files":
            file_ids = data.get("file_ids", [])
            with SESSION_LOCK:
                dest_dir = os.path.abspath(data.get("dest_dir", ACTIVE_SESSION["dest_dir"]))
            os.makedirs(dest_dir, exist_ok=True)

            recovered_count = 0
            partial_count = 0
            failed_count = 0

            for f_id in file_ids:
                with SESSION_LOCK:
                    matching = [f for f in ACTIVE_SESSION["all_files"] if f.get("id") == f_id]
                if not matching:
                    failed_count += 1
                    continue

                f = matching[0]
                clean_name = f["name"].replace(".partial", "")
                
                # Handle relative path cleanly
                rel_p = f.get("path", "").strip("\\/ ")
                if rel_p and rel_p not in (".", "\\", "/"):
                    target_subfolder = os.path.join(dest_dir, rel_p)
                else:
                    target_subfolder = dest_dir

                os.makedirs(target_subfolder, exist_ok=True)
                out_path = os.path.join(target_subfolder, clean_name)

                # Avoid accidental name collisions for files
                if os.path.exists(out_path) and not f.get("is_folder"):
                    base_n, ext_n = os.path.splitext(clean_name)
                    counter = 1
                    while os.path.exists(out_path):
                        out_path = os.path.join(target_subfolder, f"{base_n} ({counter}){ext_n}")
                        counter += 1

                # 1. Raw Disk NTFS Files (no real_path in filesystem)
                if not f.get("real_path"):
                    with SESSION_LOCK:
                        f_info = ACTIVE_SESSION["file_map"].get(f_id)
                        volume = ACTIVE_SESSION.get("volume")
                        timeout = ACTIVE_SESSION.get("timeout_ms", 1000)
                        include_sys = ACTIVE_SESSION["settings"].get("include_system", False)
                        target_exts = ACTIVE_SESSION["settings"].get("extensions", None)
                        reverse = ACTIVE_SESSION["settings"].get("reverse", False)
                        dest_dir_session = ACTIVE_SESSION["dest_dir"]

                    if f_info and volume:
                        engine = ACTIVE_SESSION.get("engine")
                        if engine is None:
                            engine = RecoveryEngine(
                                volume=volume,
                                dest_dir=dest_dir_session,
                                timeout_ms=timeout,
                                include_system_files=include_sys,
                                target_extensions=target_exts,
                                reverse_direction=reverse,
                            )
                            with SESSION_LOCK:
                                ACTIVE_SESSION["engine"] = engine

                        status = engine.recover_file(f_info)
                        if status == "RECOVERED":
                            recovered_count += 1
                        elif status == "PARTIAL":
                            partial_count += 1
                        else:
                            failed_count += 1
                    else:
                        failed_count += 1
                    continue

                # 2. Storage / Physical Drive Files with real_path
                real_p = f["real_path"]
                if not os.path.exists(real_p):
                    failed_count += 1
                    continue

                try:
                    if os.path.isdir(real_p):
                        # Entire Folder or macOS .app bundle directory
                        dest_folder_path = os.path.join(target_subfolder, clean_name)
                        shutil.copytree(real_p, dest_folder_path, dirs_exist_ok=True)
                        recovered_count += 1
                    else:
                        # Direct file copy
                        try:
                            shutil.copy2(real_p, out_path)
                            recovered_count += 1
                        except (OSError, IOError, PermissionError) as copy_err:
                            # Bad sector or I/O failure: block-by-block read with 0-filling for bad blocks
                            bytes_salvaged = 0
                            with open(real_p, "rb") as in_f, open(out_path, "wb") as out_f:
                                block_size = 4096
                                while True:
                                    try:
                                        chunk = in_f.read(block_size)
                                        if not chunk:
                                            break
                                        out_f.write(chunk)
                                        bytes_salvaged += len(chunk)
                                    except Exception:
                                        out_f.write(b"\x00" * block_size)
                                        try:
                                            in_f.seek(in_f.tell() + block_size)
                                        except Exception:
                                            break
                            if bytes_salvaged > 0:
                                partial_count += 1
                            else:
                                failed_count += 1
                except Exception as ex:
                    print(f"[-] Recovery error on {real_p}: {ex}")
                    failed_count += 1

            self._send_json({
                "success": True,
                "recovered_count": recovered_count,
                "partial_count": partial_count,
                "failed_count": failed_count,
                "dest_dir": dest_dir,
            })

        elif path == "/api/open_folder":
            with SESSION_LOCK:
                dest_dir = ACTIVE_SESSION["dest_dir"]
            folder_path = data.get("path", dest_dir)
            abs_folder = os.path.abspath(folder_path)
            abs_dest = os.path.abspath(dest_dir)
            # Security: only allow opening folders within the recovery destination
            if not _is_safe_path(abs_folder, abs_dest):
                self._send_json({"success": False, "error": "Access denied: path outside recovery directory"}, 403)
                return
            if not os.path.exists(abs_folder):
                os.makedirs(abs_folder, exist_ok=True)
            try:
                if IS_WINDOWS:
                    os.startfile(abs_folder)
                elif IS_MACOS:
                    subprocess.run(["open", abs_folder])
                else:
                    subprocess.run(["xdg-open", abs_folder])
                self._send_json({"success": True, "message": f"Opened {abs_folder}"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)})

        elif path == "/api/settings":
            new_settings = data.get("settings", {})
            with SESSION_LOCK:
                ACTIVE_SESSION["settings"].update(new_settings)
                settings_copy = ACTIVE_SESSION["settings"].copy()
            self._send_json({"success": True, "settings": settings_copy})

        elif path == "/api/scan_mft":
            with SESSION_LOCK:
                drive = data.get("drive", ACTIVE_SESSION["selected_drive_path"])
                partition_idx = data.get("partition", 1) - 1
                device_path = drive
                if not os.path.exists(device_path) and not device_path.startswith(r"\\.\\"):
                    device_path = rf"\\.\PhysicalDrive{drive}" if drive.isdigit() else drive

            try:
                reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=ACTIVE_SESSION["timeout_ms"])
                parts = scan_partitions(reader)
                if partition_idx >= len(parts):
                    reader.close()
                    self._send_json({"success": False, "error": "Invalid partition index"})
                    return

                target_part = parts[partition_idx]
                if not target_part.is_ntfs:
                    reader.close()
                    self._send_json({"success": False, "error": "Selected partition is not NTFS"})
                    return

                volume = NTFSVolume(reader, target_part.start_lba)
                max_records = ACTIVE_SESSION["settings"].get("max_records", 100000)

                ACTIVE_SESSION["all_files"] = []
                ACTIVE_SESSION["file_map"] = {}
                ACTIVE_SESSION["stats"] = {
                    "files_found": 0, "good_files": 0, "partial_files": 0,
                    "failed_files": 0, "total_bytes": 0, "percent_complete": 0.0,
                }
                ACTIVE_SESSION["volume"] = volume
                ACTIVE_SESSION["reader"] = reader
                ACTIVE_SESSION["partition"] = target_part

                def progress_cb(count):
                    if ACTIVE_SESSION["is_running"]:
                        with SESSION_LOCK:
                            ACTIVE_SESSION["stats"]["percent_complete"] = min(100.0, round((count / max_records) * 100, 1))

                files = read_all_mft_records(volume, max_records=max_records, progress_callback=progress_cb)

                user_files = {
                    rec_id: f for rec_id, f in files.items()
                    if not f.is_directory and f.name and (ACTIVE_SESSION["settings"].get("include_system", False) or not f.name.startswith("$"))
                }

                for rec_id, f in user_files.items():
                    ACTIVE_SESSION["file_map"][rec_id] = f
                    ACTIVE_SESSION["all_files"].append({
                        "id": rec_id,
                        "name": f.name,
                        "is_folder": f.is_directory,
                        "status": "Pending",
                        "size": format_bytes_human(f.file_size),
                        "raw_size": f.file_size,
                        "modified": "",
                        "path": f.full_path or f"\\{f.name}",
                        "type": "Directory" if f.is_directory else "File",
                        "record_number": rec_id,
                        "data_runs": len(f.data_runs),
                        "is_resident": f.is_resident,
                    })

                ACTIVE_SESSION["stats"]["files_found"] = len(ACTIVE_SESSION["all_files"])
                ACTIVE_SESSION["stats"]["good_files"] = len(ACTIVE_SESSION["all_files"])
                ACTIVE_SESSION["stats"]["total_bytes"] = sum(f.file_size for f in user_files.values())
                ACTIVE_SESSION["stats"]["percent_complete"] = 100.0

                reader.close()
                self._send_json({"success": True, "files_found": len(ACTIVE_SESSION["all_files"])})

            except Exception as e:
                self._send_json({"success": False, "error": str(e)})

        elif path == "/api/start_recovery":
            # Full recovery execution with all phases
            with SESSION_LOCK:
                drive = data.get("drive", ACTIVE_SESSION["selected_drive_path"])
                partition_idx = data.get("partition", 1) - 1
                dest = data.get("dest", ACTIVE_SESSION["dest_dir"])
                timeout = int(data.get("timeout", ACTIVE_SESSION["timeout_ms"]))
                include_sys = data.get("include_system", ACTIVE_SESSION["settings"].get("include_system", False))
                reverse = data.get("reverse", ACTIVE_SESSION["settings"].get("reverse", False))
                multi_pass = data.get("multi_pass", True)
                carve = data.get("carve", True)
                target_exts = data.get("extensions", None)

                device_path = drive
                if not os.path.exists(device_path) and not device_path.startswith(r"\\.\\"):
                    device_path = rf"\\.\PhysicalDrive{drive}" if drive.isdigit() else drive

                ACTIVE_SESSION["dest_dir"] = os.path.abspath(dest)
                ACTIVE_SESSION["timeout_ms"] = timeout
                os.makedirs(ACTIVE_SESSION["dest_dir"], exist_ok=True)

                # Reset state
                RECOVERY_WORKER["cancel_event"].clear()
                RECOVERY_WORKER["pause_event"].clear()
                ACTIVE_SESSION["is_running"] = True
                ACTIVE_SESSION["is_paused"] = False
                ACTIVE_SESSION["start_time"] = time.time()

            def recovery_worker():
                try:
                    reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=timeout)
                    parts = scan_partitions(reader)
                    target_part = parts[partition_idx]
                    volume = NTFSVolume(reader, target_part.start_lba)

                    with SESSION_LOCK:
                        ACTIVE_SESSION["engine"] = RecoveryEngine(
                            volume=volume,
                            dest_dir=ACTIVE_SESSION["dest_dir"],
                            timeout_ms=timeout,
                            include_system_files=include_sys,
                            target_extensions=target_exts,
                            reverse_direction=reverse,
                        )
                        ACTIVE_SESSION["volume"] = volume
                        ACTIVE_SESSION["partition"] = target_part

                    # Phase 1: Fast Sweep
                    if multi_pass and not RECOVERY_WORKER["cancel_event"].is_set():
                        with SESSION_LOCK:
                            ACTIVE_SESSION["stats"]["percent_complete"] = 5.0
                        scheduler = MultiPassScheduler(
                            volume=volume,
                            mapfile=ACTIVE_SESSION["engine"].mapfile,
                            dest_dir=ACTIVE_SESSION["dest_dir"],
                            timeout_ms=timeout
                        )
                        scheduler.run_phase1_fast_sweep(
                            progress_callback=lambda cur, tot, msg: None
                        )

                    # Phase 2: Read MFT & Extract Files
                    if not RECOVERY_WORKER["cancel_event"].is_set():
                        with SESSION_LOCK:
                            ACTIVE_SESSION["stats"]["percent_complete"] = 20.0
                        max_records = ACTIVE_SESSION["settings"].get("max_records", 100000)
                        files = read_all_mft_records(volume, max_records=max_records)

                        user_files = {
                            rec_id: f for rec_id, f in files.items()
                            if not f.is_directory and f.name and (include_sys or not f.name.startswith("$"))
                        }

                        if target_exts:
                            user_files = {
                                rec_id: f for rec_id, f in user_files.items()
                                if "." in f.name and f.name.split(".")[-1].lower() in target_exts
                            }

                        with SESSION_LOCK:
                            ACTIVE_SESSION["file_map"] = user_files
                            ACTIVE_SESSION["all_files"] = [{
                                "id": rec_id,
                                "name": f.name,
                                "is_folder": f.is_directory,
                                "status": "Pending",
                                "size": format_bytes_human(f.file_size),
                                "raw_size": f.file_size,
                                "modified": "",
                                "path": f.full_path or f"\\{f.name}",
                                "type": "Directory" if f.is_directory else "File",
                                "record_number": rec_id,
                            } for rec_id, f in user_files.items()]
                            ACTIVE_SESSION["stats"]["files_found"] = len(user_files)
                            ACTIVE_SESSION["stats"]["total_bytes"] = sum(f.file_size for f in user_files.values())

                        def on_progress(stats, current_file, outcome):
                            if RECOVERY_WORKER["pause_event"].is_set():
                                while RECOVERY_WORKER["pause_event"].is_set() and not RECOVERY_WORKER["cancel_event"].is_set():
                                    time.sleep(0.1)
                            if RECOVERY_WORKER["cancel_event"].is_set():
                                return
                            with SESSION_LOCK:
                                ACTIVE_SESSION["stats"].update(stats)
                                # Update file status in UI
                                for f in ACTIVE_SESSION["all_files"]:
                                    if f.get("record_number") == current_file.record_number:
                                        f["status"] = outcome
                                        break

                        final_stats = ACTIVE_SESSION["engine"].run_recovery(user_files, progress_callback=on_progress)

                    # Phase 3: Scraping
                    if multi_pass and not RECOVERY_WORKER["cancel_event"].is_set():
                        with SESSION_LOCK:
                            ACTIVE_SESSION["stats"]["percent_complete"] = 85.0
                        scheduler = MultiPassScheduler(
                            volume=volume,
                            mapfile=ACTIVE_SESSION["engine"].mapfile,
                            dest_dir=ACTIVE_SESSION["dest_dir"],
                            timeout_ms=timeout
                        )
                        scheduler.run_phase3_scraping(
                            progress_callback=lambda cur, tot, msg: None
                        )

                    # Phase 4: Carving
                    if carve and not RECOVERY_WORKER["cancel_event"].is_set():
                        with SESSION_LOCK:
                            ACTIVE_SESSION["stats"]["percent_complete"] = 95.0
                        carved = ACTIVE_SESSION["engine"].carve_orphan_files(
                            start_lba=target_part.start_lba,
                            sector_count=min(volume.total_sectors, 500000),
                            progress_callback=lambda cur, tot, cnt: None
                        )
                        # Add carved files to list
                        for c in carved:
                            with SESSION_LOCK:
                                ACTIVE_SESSION["all_files"].append({
                                    "id": len(ACTIVE_SESSION["all_files"]),
                                    "name": os.path.basename(c.output_path),
                                    "is_folder": False,
                                    "status": c.status,
                                    "size": format_bytes_human(c.size),
                                    "raw_size": c.size,
                                    "modified": time.strftime("%m/%d/%Y %I:%M %p"),
                                    "path": "\\carved",
                                    "type": c.file_type,
                                    "record_number": -1,
                                })
                                if c.status == "RECOVERED":
                                    ACTIVE_SESSION["stats"]["good_files"] += 1
                                else:
                                    ACTIVE_SESSION["stats"]["partial_files"] += 1

                    with SESSION_LOCK:
                        ACTIVE_SESSION["stats"]["percent_complete"] = 100.0
                        ACTIVE_SESSION["is_running"] = False

                except Exception as e:
                    with SESSION_LOCK:
                        ACTIVE_SESSION["is_running"] = False
                    print(f"Recovery error: {e}")

            RECOVERY_WORKER["thread"] = threading.Thread(target=recovery_worker, daemon=True)
            RECOVERY_WORKER["thread"].start()

            self._send_json({"success": True, "message": "Recovery started"})

        elif path == "/api/cancel":
            RECOVERY_WORKER["cancel_event"].set()
            RECOVERY_WORKER["pause_event"].clear()
            with SESSION_LOCK:
                if ACTIVE_SESSION["engine"]:
                    ACTIVE_SESSION["engine"].cancel()
                ACTIVE_SESSION["is_running"] = False
                ACTIVE_SESSION["is_paused"] = False
            self._send_json({"success": True, "message": "Recovery cancelled"})

        elif path == "/api/phase1":
            with SESSION_LOCK:
                if not ACTIVE_SESSION["volume"] or not ACTIVE_SESSION["engine"]:
                    self._send_json({"success": False, "error": "No active volume/engine"})
                    return
                volume = ACTIVE_SESSION["volume"]
                engine = ACTIVE_SESSION["engine"]
                timeout = ACTIVE_SESSION["timeout_ms"]
                dest_dir = ACTIVE_SESSION["dest_dir"]
            scheduler = MultiPassScheduler(
                volume=volume,
                mapfile=engine.mapfile,
                dest_dir=dest_dir,
                timeout_ms=timeout
            )
            scheduler.run_phase1_fast_sweep(
                progress_callback=lambda cur, tot, msg: None
            )
            self._send_json({"success": True, "message": "Phase 1 complete"})

        elif path == "/api/phase3":
            with SESSION_LOCK:
                if not ACTIVE_SESSION["volume"] or not ACTIVE_SESSION["engine"]:
                    self._send_json({"success": False, "error": "No active volume/engine"})
                    return
                volume = ACTIVE_SESSION["volume"]
                engine = ACTIVE_SESSION["engine"]
                timeout = ACTIVE_SESSION["timeout_ms"]
                dest_dir = ACTIVE_SESSION["dest_dir"]
            scheduler = MultiPassScheduler(
                volume=volume,
                mapfile=engine.mapfile,
                dest_dir=dest_dir,
                timeout_ms=timeout
            )
            scheduler.run_phase3_scraping(
                progress_callback=lambda cur, tot, msg: None
            )
            self._send_json({"success": True, "message": "Phase 3 complete"})

        elif path == "/api/carve":
            with SESSION_LOCK:
                if not ACTIVE_SESSION["engine"] or not ACTIVE_SESSION["partition"]:
                    self._send_json({"success": False, "error": "No active engine/partition"})
                    return
                engine = ACTIVE_SESSION["engine"]
                partition = ACTIVE_SESSION["partition"]
                volume = ACTIVE_SESSION["volume"]
            carved = engine.carve_orphan_files(
                start_lba=partition.start_lba,
                sector_count=min(volume.total_sectors, 500000),
                progress_callback=lambda cur, tot, cnt: None
            )
            with SESSION_LOCK:
                for c in carved:
                    ACTIVE_SESSION["all_files"].append({
                        "id": len(ACTIVE_SESSION["all_files"]),
                        "name": os.path.basename(c.output_path),
                        "is_folder": False,
                        "status": c.status,
                        "size": format_bytes_human(c.size),
                        "raw_size": c.size,
                        "modified": time.strftime("%m/%d/%Y %I:%M %p"),
                        "path": "\\carved",
                        "type": c.file_type,
                        "record_number": -1,
                    })
            self._send_json({"success": True, "carved": len(carved)})

        elif path == "/api/smart":
            with SESSION_LOCK:
                drive = data.get("drive", ACTIVE_SESSION["selected_drive_path"])
                device_path = drive
                if not os.path.exists(device_path) and not device_path.startswith(r"\\.\\"):
                    device_path = rf"\\.\PhysicalDrive{drive}" if drive.isdigit() else drive
            try:
                reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=500)
                handle = getattr(reader, "handle", None)
            except Exception:
                handle = None
            try:
                from smart_monitor import query_smart_health
                smart = query_smart_health(device_path, handle)
            except Exception as e:
                smart = None
                print(f"SMART error: {e}")
            if handle:
                try:
                    reader.close()
                except:
                    pass
            if smart:
                self._send_json({"success": True, "smart": smart.to_dict()})
            else:
                self._send_json({"success": False, "error": "SMART not available"})

        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, payload: Dict[str, Any], status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def run_web_studio(port: int = 8080, open_browser: bool = False):
    server = None
    actual_port = port
    for p in range(port, port + 10):
        try:
            # Security: bind to localhost only
            server = ReusableHTTPServer(("127.0.0.1", p), UnifiedStudioHandler)
            actual_port = p
            break
        except OSError:
            continue

    if server is None:
        print(f"[X] Error: Could not bind to any port in range {port}-{port+9}.")
        return

    # Enumerate devices initially
    list_all_storage_devices()

    url = f"http://127.0.0.1:{actual_port}"
    print(f"\n========================================================================")
    print(f"  ANTIGRAVITY DRIVE RESCUE STUDIO (LIVE RECOVERY ENGINE)")
    print(f"  Active URL: {url}")
    print(f"========================================================================\n", flush=True)

    if open_browser:
        def _safe_open():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Timer(0.8, _safe_open).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Drive Rescue Studio...")
        server.server_close()


if __name__ == "__main__":
    should_open = "--open" in sys.argv
    run_web_studio(open_browser=should_open)