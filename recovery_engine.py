"""
recovery_engine.py - Core Fault-Tolerant File Recovery Engine with Mapfile & Forensic Verification

Executes sequential and multi-pass sector-aligned file extraction from failing NTFS volumes.
Includes S.M.A.R.T. hardware telemetry, streaming SHA-256 cryptographic hashing,
priority extension filtering, reverse direction mode, and automated audit report generation.
"""

import os
import time
import csv
import logging
import threading
from typing import Dict, List, Optional, Callable, Any

from ntfs_parser import NTFSVolume, NTFSFileInfo, DataRun
from raw_io import RawDiskReader
from mapfile import (
    RecoveryMapFile,
    STATE_UNTOUCHED,
    STATE_RECOVERED,
    STATE_SKIPPED,
    STATE_BAD,
    STATE_SCRAPED,
)
from watchdog import WatchdogDiskReader
from file_carver import FileCarver, CarvedFile
from hash_verifier import StreamHasher
from smart_monitor import query_smart_health, SmartHealthReport
from audit_report import generate_audit_report_html


class RecoveryStats:
    """Thread-safe recovery metrics and sector counters."""
    def __init__(self):
        self.lock = threading.Lock()
        self.total_files = 0
        self.processed_files = 0
        self.recovered_files = 0
        self.partial_files = 0
        self.failed_files = 0
        self.skipped_files = 0
        self.good_sectors = 0
        self.bad_sectors = 0
        self.total_bytes_written = 0
        self.start_time = time.time()
        self.is_cancelled = False

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            elapsed = max(1e-3, time.time() - self.start_time)
            pct = (self.processed_files / self.total_files * 100.0) if self.total_files > 0 else 0.0
            return {
                "total_files": self.total_files,
                "processed_files": self.processed_files,
                "percent_complete": pct,
                "recovered_files": self.recovered_files,
                "partial_files": self.partial_files,
                "failed_files": self.failed_files,
                "skipped_files": self.skipped_files,
                "good_sectors": self.good_sectors,
                "bad_sectors": self.bad_sectors,
                "total_bytes_written": self.total_bytes_written,
                "elapsed_seconds": elapsed,
            }


class RecoveryEngine:
    """
    Coordinates file extraction, bad sector zero-filling, forensic hashing, and audit reporting.
    """
    def __init__(
        self,
        volume: NTFSVolume,
        dest_dir: str,
        timeout_ms: int = 1000,
        include_system_files: bool = False,
        target_extensions: Optional[List[str]] = None,
        reverse_direction: bool = False,
    ):
        self.volume = volume
        self.raw_reader = volume.reader
        self.reader = WatchdogDiskReader(volume.reader, hard_timeout_margin_ms=500)
        self.dest_dir = os.path.abspath(dest_dir)
        self.timeout_ms = timeout_ms
        self.include_system_files = include_system_files
        self.target_extensions = [ext.lower().strip(".") for ext in target_extensions] if target_extensions else None
        self.reverse_direction = reverse_direction
        self.stats = RecoveryStats()
        self.manifest_records: List[Dict[str, Any]] = []

        os.makedirs(self.dest_dir, exist_ok=True)

        # Log & mapfile paths
        self.mapfile_path = os.path.join(self.dest_dir, "recovery.map")
        self.log_file_path = os.path.join(self.dest_dir, "recovery.log")
        self.bad_sectors_path = os.path.join(self.dest_dir, "bad_sectors.log")
        self.manifest_path = os.path.join(self.dest_dir, "recovery_manifest.csv")
        self.audit_report_path = os.path.join(self.dest_dir, "recovery_audit_report.html")

        # Query S.M.A.R.T. Hardware Pre-Flight Telemetry
        self.smart_report: Optional[SmartHealthReport] = query_smart_health(
            self.raw_reader.device_path,
            handle=getattr(self.raw_reader, "handle", None),
        )

        total_sectors = volume.total_sectors if volume.total_sectors > 0 else 2048000
        self.mapfile = RecoveryMapFile(self.mapfile_path, total_sectors=total_sectors)

        self._setup_logging()

    def _setup_logging(self):
        """Initializes recovery loggers."""
        self.logger = logging.getLogger(f"RecoveryEngine_{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        fh = logging.FileHandler(self.log_file_path, mode="a", encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        # Bad sectors log file header
        if not os.path.exists(self.bad_sectors_path):
            with open(self.bad_sectors_path, "w", encoding="utf-8") as f:
                f.write("# Bad Sector LBA Log\n# Timestamp, Sector LBA, File Path\n")

        # Manifest CSV header
        if not os.path.exists(self.manifest_path):
            with open(self.manifest_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "RecordNumber",
                    "OriginalPath",
                    "Status",
                    "FileSize",
                    "GoodClusters",
                    "BadClusters",
                    "SHA256",
                    "OutputPath",
                ])

    def log_bad_sector(self, sector_lba: int, file_path: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.bad_sectors_path, "a", encoding="utf-8") as f:
            f.write(f"{timestamp}, {sector_lba}, \"{file_path}\"\n")

    def cancel(self):
        self.stats.is_cancelled = True
        self.logger.info("Recovery cancellation requested by user.")

    def sanitize_path(self, rel_path: str) -> str:
        parts = rel_path.replace("\\", "/").split("/")
        clean_parts = []
        for p in parts:
            p = p.strip()
            if not p or p == ".":
                continue
            for ch in '<>:"/\\|?*':
                p = p.replace(ch, "_")
            clean_parts.append(p)

        if not clean_parts:
            return "unnamed_file"
        return os.path.join(*clean_parts)

    def recover_file(self, file_info: NTFSFileInfo) -> str:
        """
        Recovers a single file with streaming SHA-256 hash calculation and zero-filling.
        """
        if file_info.is_directory:
            return "SKIPPED"

        if not self.include_system_files and (file_info.name.startswith("$") or file_info.record_number < 16):
            return "SKIPPED"

        # Check target extension filter
        if self.target_extensions:
            ext = file_info.name.split(".")[-1].lower() if "." in file_info.name else ""
            if ext not in self.target_extensions:
                return "SKIPPED"

        rel_path = self.sanitize_path(file_info.full_path if file_info.full_path else file_info.name)
        out_path = os.path.join(self.dest_dir, rel_path)
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        hasher = StreamHasher()

        # 1. Handle Resident Files
        if file_info.is_resident:
            try:
                data = file_info.resident_data if file_info.resident_data else b""
                data_to_write = data[:file_info.file_size]
                hasher.update(data_to_write)
                with open(out_path, "wb") as f:
                    f.write(data_to_write)

                with self.stats.lock:
                    self.stats.recovered_files += 1
                    self.stats.total_bytes_written += len(data_to_write)

                self._record_manifest(file_info, "RECOVERED", 0, 0, hasher.sha256_hex, out_path)
                return "RECOVERED"
            except Exception as e:
                self.logger.error(f"Failed writing resident file {file_info.full_path}: {e}")
                with self.stats.lock:
                    self.stats.failed_files += 1
                self._record_manifest(file_info, "FAILED", 0, 0, "", "")
                return "FAILED"

        # 2. Handle Non-Resident Files
        if not file_info.data_runs:
            try:
                with open(out_path, "wb") as f:
                    pass
                with self.stats.lock:
                    self.stats.recovered_files += 1
                self._record_manifest(file_info, "RECOVERED", 0, 0, hasher.sha256_hex, out_path)
                return "RECOVERED"
            except Exception:
                with self.stats.lock:
                    self.stats.failed_files += 1
                self._record_manifest(file_info, "FAILED", 0, 0, "", "")
                return "FAILED"

        good_clusters = 0
        bad_clusters = 0
        bytes_written = 0
        target_size = file_info.file_size
        cluster_size = self.volume.bytes_per_cluster
        sec_per_cluster = self.volume.sectors_per_cluster
        zero_cluster = b"\x00" * cluster_size

        try:
            with open(out_path, "wb") as out_file:
                runs = file_info.data_runs

                for run in runs:
                    if self.stats.is_cancelled:
                        break

                    if run.is_sparse or run.lcn is None:
                        for _ in range(run.cluster_count):
                            if bytes_written >= target_size:
                                break
                            write_len = min(cluster_size, target_size - bytes_written)
                            chunk = b"\x00" * write_len
                            out_file.write(chunk)
                            hasher.update(chunk)
                            bytes_written += write_len
                        continue

                    # Read physical clusters
                    for c_idx in range(run.cluster_count):
                        if self.stats.is_cancelled:
                            break
                        if bytes_written >= target_size:
                            break

                        current_lcn = run.lcn + c_idx
                        start_sec = self.volume.lcn_to_sector(current_lcn)

                        cluster_data = self.reader.read_cluster(
                            self.volume.partition_start_lba,
                            current_lcn,
                            sec_per_cluster,
                            timeout_ms=self.timeout_ms,
                        )
                        write_len = min(cluster_size, target_size - bytes_written)

                        if cluster_data and len(cluster_data) >= write_len:
                            chunk = cluster_data[:write_len]
                            out_file.write(chunk)
                            hasher.update(chunk)
                            bytes_written += write_len
                            good_clusters += 1
                            self.mapfile.mark_range(start_sec, sec_per_cluster, STATE_RECOVERED)
                            with self.stats.lock:
                                self.stats.good_sectors += sec_per_cluster
                                self.stats.total_bytes_written += write_len
                        else:
                            # Bad / Timeout sector -> Zero-fill
                            chunk = zero_cluster[:write_len]
                            out_file.write(chunk)
                            hasher.update(chunk)
                            bytes_written += write_len
                            bad_clusters += 1
                            self.mapfile.mark_range(start_sec, sec_per_cluster, STATE_BAD)
                            with self.stats.lock:
                                self.stats.bad_sectors += sec_per_cluster
                                self.stats.total_bytes_written += write_len

                            for s in range(sec_per_cluster):
                                self.log_bad_sector(start_sec + s, file_info.full_path)

                out_file.truncate(target_size)

        except Exception as e:
            self.logger.error(f"Error during extraction of {file_info.full_path}: {e}")
            with self.stats.lock:
                self.stats.failed_files += 1
            self._record_manifest(file_info, "FAILED", good_clusters, bad_clusters, "", out_path)
            return "FAILED"

        # Categorize file outcome
        sha_hash = hasher.sha256_hex
        if bad_clusters == 0:
            status = "RECOVERED"
            with self.stats.lock:
                self.stats.recovered_files += 1
            self.logger.info(f"[RECOVERED] {file_info.full_path} ({target_size} bytes, SHA256: {sha_hash[:8]}...)")
        elif good_clusters > 0:
            status = "PARTIAL"
            partial_out_path = out_path + ".partial"
            try:
                if os.path.exists(out_path):
                    if os.path.exists(partial_out_path):
                        os.remove(partial_out_path)
                    os.rename(out_path, partial_out_path)
                    out_path = partial_out_path
            except Exception:
                pass

            with self.stats.lock:
                self.stats.partial_files += 1
            self.logger.warning(
                f"[PARTIAL] {file_info.full_path} - {bad_clusters} bad clusters zero-filled"
            )
        else:
            status = "FAILED"
            with self.stats.lock:
                self.stats.failed_files += 1
            self.logger.error(f"[FAILED] {file_info.full_path} - all clusters unreadable")

        self._record_manifest(file_info, status, good_clusters, bad_clusters, sha_hash, out_path)
        return status

    def _record_manifest(self, file_info: NTFSFileInfo, status: str, good_c: int, bad_c: int, sha256_hash: str, out_path: str):
        record = {
            "RecordNumber": file_info.record_number,
            "OriginalPath": file_info.full_path or file_info.name,
            "Status": status,
            "FileSize": file_info.file_size,
            "GoodClusters": good_c,
            "BadClusters": bad_c,
            "SHA256": sha256_hash,
            "OutputPath": out_path,
        }
        self.manifest_records.append(record)

        with open(self.manifest_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                record["RecordNumber"],
                record["OriginalPath"],
                record["Status"],
                record["FileSize"],
                record["GoodClusters"],
                record["BadClusters"],
                record["SHA256"],
                record["OutputPath"],
            ])

    def carve_orphan_files(
        self,
        start_lba: int = 0,
        sector_count: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, int], None]] = None,
    ) -> List[CarvedFile]:
        carver = FileCarver(self.raw_reader, self.dest_dir, timeout_ms=self.timeout_ms)
        count = sector_count if sector_count is not None else self.volume.total_sectors
        return carver.carve_sectors(start_lba, count, progress_callback=progress_callback)

    def generate_audit_certificate(self):
        """Generates the professional client audit certificate HTML."""
        drive_info = {
            "name": getattr(self.raw_reader, "name", "Physical Drive"),
            "device_path": self.raw_reader.device_path,
            "size_bytes": self.raw_reader.disk_size_bytes,
        }
        generate_audit_report_html(
            report_path=self.audit_report_path,
            drive_info=drive_info,
            smart_report=self.smart_report,
            stats=self.stats.to_dict(),
            manifest_records=self.manifest_records,
        )

    def run_recovery(
        self,
        files: Dict[int, NTFSFileInfo],
        progress_callback: Optional[Callable[[Dict[str, Any], NTFSFileInfo, str], None]] = None,
    ) -> RecoveryStats:
        target_files = [f for f in files.values() if not f.is_directory and f.name]

        # Filter by extensions if provided
        if self.target_extensions:
            target_files = [
                f for f in target_files
                if ("." in f.name and f.name.split(".")[-1].lower() in self.target_extensions)
            ]

        if self.reverse_direction:
            target_files.reverse()

        self.stats.total_files = len(target_files)

        self.logger.info(f"Starting recovery of {len(target_files)} files to {self.dest_dir}")
        self.logger.info(f"S.M.A.R.T. Health: {self.smart_report.health_verdict if self.smart_report else 'N/A'}")
        self.logger.info(f"Read Timeout: {self.timeout_ms}ms | Reverse: {self.reverse_direction}")

        file_counter = 0
        for f_info in target_files:
            if self.stats.is_cancelled:
                self.logger.info("Recovery aborted by user.")
                break

            status = self.recover_file(f_info)

            with self.stats.lock:
                self.stats.processed_files += 1

            file_counter += 1
            if file_counter % 25 == 0:
                self.mapfile.save()

            if progress_callback:
                progress_callback(self.stats.to_dict(), f_info, status)

        self.mapfile.save()
        self.generate_audit_certificate()
        self.logger.info(f"Audit certificate generated at: {self.audit_report_path}")
        self.logger.info(f"Stats: {self.stats.to_dict()}")
        return self.stats
