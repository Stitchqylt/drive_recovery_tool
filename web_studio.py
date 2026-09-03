"""
web_studio.py - Lightweight Zero-Dependency Local Web Studio Server

Serves the modern Roboflow-style recovery studio dashboard over a local HTTP server (http://127.0.0.1:8080)
and exposes REST API endpoints for drive enumeration, S.M.A.R.T. telemetry, scanning, live sector heatmap, and file streaming.
"""

import os
import sys
import json
import time
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

from raw_io import RawDiskReader, list_physical_drives, is_admin
from disk_layout import scan_partitions
from ntfs_parser import NTFSVolume, read_all_mft_records
from recovery_engine import RecoveryEngine
from smart_monitor import query_smart_health

ACTIVE_SESSION = {
    "engine": None,
    "volume": None,
    "reader": None,
    "is_running": False,
    "stats": {},
    "logs": [],
    "recent_files": [],
    "mapfile_stats": {},
}


class StudioHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress standard HTTP request log spam
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            local_studio = os.path.join(os.path.dirname(__file__), "studio.html")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                with open(local_studio, "rb") as f:
                    self.wfile.write(f.read())
            except Exception as e:
                self.wfile.write(f"<h1>Error loading studio template: {e}</h1>".encode("utf-8"))

        elif self.path == "/api/drives":
            drives = list_physical_drives()
            self._send_json({"drives": drives, "is_admin": is_admin()})

        elif self.path.startswith("/api/smart"):
            query = self.path.split("?")[-1]
            drive_val = "1"
            for q in query.split("&"):
                if q.startswith("drive="):
                    drive_val = q.split("=")[1]

            device_path = rf"\\.\PhysicalDrive{drive_val}" if drive_val.isdigit() else drive_val
            smart_rep = query_smart_health(device_path)
            self._send_json(smart_rep.to_dict())

        elif self.path.startswith("/api/partitions"):
            query = self.path.split("?")[-1]
            drive_idx = "0"
            for q in query.split("&"):
                if q.startswith("drive="):
                    drive_idx = q.split("=")[1]

            device_path = rf"\\.\PhysicalDrive{drive_idx}" if drive_idx.isdigit() else drive_idx
            try:
                reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=1000)
                parts = scan_partitions(reader)
                part_list = [{
                    "index": p.index,
                    "name": p.name,
                    "start_lba": p.start_lba,
                    "size_gb": p.size_gb,
                    "is_ntfs": p.is_ntfs,
                } for p in parts]
                reader.close()
                self._send_json({"success": True, "partitions": part_list})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "partitions": []})

        elif self.path == "/api/status":
            engine = ACTIVE_SESSION["engine"]
            if engine:
                st = engine.stats.to_dict()
                map_st = engine.mapfile.get_stats()
                self._send_json({
                    "is_running": ACTIVE_SESSION["is_running"],
                    "stats": st,
                    "map_stats": map_st,
                    "recent_files": ACTIVE_SESSION["recent_files"][-50:],
                    "logs": ACTIVE_SESSION["logs"][-30:],
                })
            else:
                self._send_json({
                    "is_running": ACTIVE_SESSION["is_running"],
                    "stats": None,
                    "map_stats": None,
                    "recent_files": [],
                    "logs": ACTIVE_SESSION["logs"][-10:],
                })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/start":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            data = json.loads(body.decode("utf-8")) if body else {}

            drive = data.get("drive", "1")
            dest = data.get("dest", "recovered_files")
            timeout = int(data.get("timeout", 1000))
            multi_pass = bool(data.get("multi_pass", False))
            part_idx = int(data.get("partition", 1))

            device_path = rf"\\.\PhysicalDrive{drive}" if str(drive).isdigit() else str(drive)

            def _start_worker():
                ACTIVE_SESSION["is_running"] = True
                ACTIVE_SESSION["recent_files"] = []
                ACTIVE_SESSION["logs"] = []
                try:
                    ACTIVE_SESSION["logs"].append(f"Opening target device: {device_path}")
                    reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=timeout)
                    parts = scan_partitions(reader)
                    ntfs_parts = [p for p in parts if p.is_ntfs]
                    target_lba = ntfs_parts[0].start_lba if ntfs_parts else (parts[0].start_lba if parts else 2048)

                    volume = NTFSVolume(reader, target_lba)
                    engine = RecoveryEngine(volume, dest, timeout_ms=timeout)
                    ACTIVE_SESSION["engine"] = engine
                    ACTIVE_SESSION["volume"] = volume
                    ACTIVE_SESSION["reader"] = reader

                    ACTIVE_SESSION["logs"].append("Parsing Master File Table ($MFT)...")
                    files = read_all_mft_records(volume, max_records=100000)
                    user_files = {r: f for r, f in files.items() if not f.is_directory and f.name}
                    ACTIVE_SESSION["logs"].append(f"Found {len(user_files)} recoverable files. Starting stream...")

                    def on_prog(stats, f_info, outcome):
                        ACTIVE_SESSION["recent_files"].append({
                            "path": f_info.full_path or f_info.name,
                            "size": f_info.file_size,
                            "status": outcome,
                        })

                    engine.run_recovery(user_files, progress_callback=on_prog)
                    ACTIVE_SESSION["logs"].append("Recovery finished successfully.")
                except Exception as e:
                    ACTIVE_SESSION["logs"].append(f"Fatal error: {e}")
                finally:
                    ACTIVE_SESSION["is_running"] = False

            t = threading.Thread(target=_start_worker, daemon=True)
            t.start()
            self._send_json({"success": True, "message": "Recovery started"})

        elif self.path == "/api/pause":
            if ACTIVE_SESSION["engine"]:
                ACTIVE_SESSION["engine"].cancel()
            ACTIVE_SESSION["is_running"] = False
            self._send_json({"success": True, "message": "Pausing recovery session"})
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, payload: Dict[str, Any]):
        self.send_response(200)
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
            server = ReusableHTTPServer(("0.0.0.0", p), StudioHandler)
            actual_port = p
            break
        except OSError:
            continue

    if server is None:
        print(f"[X] Error: Could not bind to any port in range {port}-{port+9}.")
        return

    url = f"http://127.0.0.1:{actual_port}"
    print(f"\n========================================================================")
    print(f"  ANTIGRAVITY RECOVERY STUDIO (WEB DASHBOARD)")
    print(f"  Running locally at: {url}")
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
        print("\nStopping Web Studio...")
        server.server_close()


if __name__ == "__main__":
    should_open = "--open" in sys.argv
    run_web_studio(open_browser=should_open)
