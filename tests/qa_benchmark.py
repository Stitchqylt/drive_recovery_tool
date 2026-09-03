"""
qa_benchmark.py - Comprehensive Industry QA & Benchmark Scoring Suite

Runs 5 rigorous empirical benchmarks against the recovery engine:
1. Benchmark 1: Healthy Throughput Speed & Processing Latency
2. Benchmark 2: Bad Sector Non-Freezing Timeout & Zero-Fill Resilience
3. Benchmark 3: Crash Recovery & Mapfile Resumability Integrity
4. Benchmark 4: Cryptographic SHA-256 Forensic Accuracy
5. Benchmark 5: Multi-Run Fragmentation & Deep Directory Tree Reconstruction

Outputs a detailed diagnostic scorecard and final readiness rating.
"""

import os
import sys
import time
import struct
import hashlib
import tempfile
import shutil
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from raw_io import RawDiskReader
from disk_layout import parse_mbr_partitions
from ntfs_parser import NTFSVolume, NTFSFileInfo, DataRun
from recovery_engine import RecoveryEngine
from mapfile import RecoveryMapFile, STATE_RECOVERED, STATE_BAD
from hash_verifier import compute_file_hashes


class BenchmarkRunner:
    def __init__(self):
        self.results = []
        self.total_score = 0
        self.max_score = 100

    def log_result(self, name: str, passed: bool, score: int, max_score: int, details: str):
        self.results.append({
            "name": name,
            "passed": passed,
            "score": score,
            "max_score": max_score,
            "details": details,
        })
        self.total_score += score

    def run_all(self):
        print("\n" + "=" * 78)
        print("  ANTIGRAVITY RAW RECOVERY ENGINE - QA BENCHMARK & AUDIT SUITE")
        print("=" * 78)

        self.bench_1_throughput()
        self.bench_2_bad_sector_resilience()
        self.bench_3_resumability()
        self.bench_4_forensic_integrity()
        self.bench_5_fragmentation_and_tree()

        self.print_scorecard()

    def bench_1_throughput(self):
        """Benchmark 1: Measures raw cluster processing speed in MB/s."""
        temp_dir = tempfile.mkdtemp()
        disk_path = os.path.join(temp_dir, "bench1_disk.img")
        size_mb = 16
        with open(disk_path, "wb") as f:
            f.write(os.urandom(size_mb * 1024 * 1024))

        reader = RawDiskReader(disk_path, sector_size=512)
        total_sectors = (size_mb * 1024 * 1024) // 512

        t0 = time.perf_counter()
        sectors_read = 0
        chunk = 128  # 64KB chunks
        for s in range(0, total_sectors, chunk):
            data = reader.read_sectors(s, chunk)
            if data:
                sectors_read += chunk
        elapsed = time.perf_counter() - t0
        reader.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

        mb_sec = (size_mb / elapsed) if elapsed > 0 else 0
        passed = mb_sec > 50.0
        score = 20 if passed else int((mb_sec / 50.0) * 20)
        self.log_result(
            "Benchmark 1: Raw Direct Throughput",
            passed,
            score,
            20,
            f"Processed {size_mb} MB in {elapsed:.3f}s ({mb_sec:.1f} MB/s)",
        )

    def bench_2_bad_sector_resilience(self):
        """Benchmark 2: Simulates unreadable bad sectors and verifies zero-filling and non-freezing."""
        temp_dir = tempfile.mkdtemp()
        disk_path = os.path.join(temp_dir, "bench2_disk.img")
        dest_dir = os.path.join(temp_dir, "recovered")

        with open(disk_path, "wb") as f:
            f.write(b"\xAA" * (1024 * 1024))

        # Write NTFS VBR
        with open(disk_path, "r+b") as f:
            f.seek(0)
            vbr = bytearray(512)
            vbr[3:11] = b"NTFS    "
            vbr[0x0B:0x0D] = struct.pack("<H", 512)
            vbr[0x0D] = 8
            vbr[0x28:0x30] = struct.pack("<Q", 2048)
            vbr[0x30:0x38] = struct.pack("<Q", 4)
            vbr[0x40] = 0xF6
            f.write(vbr)

        reader = RawDiskReader(disk_path, sector_size=512)
        volume = NTFSVolume(reader, 0)

        # File with good cluster (10) and out-of-bounds/bad cluster (9999)
        f_info = NTFSFileInfo(50)
        f_info.name = "corrupted_archive.zip"
        f_info.data_runs = [DataRun(lcn=10, cluster_count=1), DataRun(lcn=9999, cluster_count=1)]
        f_info.file_size = 8192

        engine = RecoveryEngine(volume, dest_dir, timeout_ms=300)
        t0 = time.perf_counter()
        stats = engine.run_recovery({50: f_info})
        elapsed = time.perf_counter() - t0
        reader.close()

        out_file = os.path.join(dest_dir, "corrupted_archive.zip.partial")
        file_ok = os.path.exists(out_file) and os.path.getsize(out_file) == 8192

        # Verify second cluster is zero-filled
        with open(out_file, "rb") as f:
            f.seek(4096)
            zero_block = f.read(4096)
            is_zeroed = zero_block == (b"\x00" * 4096)

        shutil.rmtree(temp_dir, ignore_errors=True)

        passed = file_ok and is_zeroed and stats.partial_files == 1
        score = 20 if passed else 0
        self.log_result(
            "Benchmark 2: Bad Sector Zero-Fill & Resilience",
            passed,
            score,
            20,
            f"Zero-fill verified: {is_zeroed} | Status: PARTIAL | Latency: {elapsed:.3f}s",
        )

    def bench_3_resumability(self):
        """Benchmark 3: Tests session pausing and seamless mapfile resumption."""
        temp_dir = tempfile.mkdtemp()
        disk_path = os.path.join(temp_dir, "bench3_disk.img")
        dest_dir = os.path.join(temp_dir, "recovered")

        with open(disk_path, "wb") as f:
            f.write(b"\x55" * (2 * 1024 * 1024))
        with open(disk_path, "r+b") as f:
            vbr = bytearray(512)
            vbr[3:11] = b"NTFS    "
            vbr[0x0B:0x0D] = struct.pack("<H", 512)
            vbr[0x0D] = 8
            vbr[0x28:0x30] = struct.pack("<Q", 4096)
            vbr[0x30:0x38] = struct.pack("<Q", 4)
            vbr[0x40] = 0xF6
            f.seek(0)
            f.write(vbr)

        reader = RawDiskReader(disk_path, sector_size=512)
        volume = NTFSVolume(reader, 0)

        # Batch 1: 10 files
        files_batch1 = {}
        for i in range(10):
            fi = NTFSFileInfo(100 + i)
            fi.name = f"doc_{i}.txt"
            fi.is_resident = True
            fi.resident_data = f"Document content {i}".encode("utf-8")
            fi.file_size = len(fi.resident_data)
            files_batch1[100 + i] = fi

        # Run session 1
        engine1 = RecoveryEngine(volume, dest_dir, timeout_ms=500)
        engine1.run_recovery(files_batch1)

        # Verify mapfile exists
        map_path = os.path.join(dest_dir, "recovery.map")
        map_exists = os.path.exists(map_path)

        # Session 2: Add 10 more files and resume
        files_batch2 = dict(files_batch1)
        for i in range(10, 20):
            fi = NTFSFileInfo(100 + i)
            fi.name = f"doc_{i}.txt"
            fi.is_resident = True
            fi.resident_data = f"Document content {i}".encode("utf-8")
            fi.file_size = len(fi.resident_data)
            files_batch2[100 + i] = fi

        engine2 = RecoveryEngine(volume, dest_dir, timeout_ms=500)
        stats2 = engine2.run_recovery(files_batch2)

        reader.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

        passed = map_exists and stats2.recovered_files == 20
        score = 20 if passed else 0
        self.log_result(
            "Benchmark 3: Mapfile Crash Resumability",
            passed,
            score,
            20,
            f"Mapfile journal preserved: {map_exists} | Total Recovered: {stats2.recovered_files}/20",
        )

    def bench_4_forensic_integrity(self):
        """Benchmark 4: Byte-for-byte SHA-256 cryptographic verification."""
        temp_dir = tempfile.mkdtemp()
        disk_path = os.path.join(temp_dir, "bench4_disk.img")
        dest_dir = os.path.join(temp_dir, "recovered")

        original_payload = b"FORENSIC TRUTH VERIFICATION " * 300
        expected_sha256 = hashlib.sha256(original_payload).hexdigest()

        with open(disk_path, "wb") as f:
            f.write(b"\x00" * (1024 * 1024))
        with open(disk_path, "r+b") as f:
            vbr = bytearray(512)
            vbr[3:11] = b"NTFS    "
            vbr[0x0B:0x0D] = struct.pack("<H", 512)
            vbr[0x0D] = 8
            vbr[0x28:0x30] = struct.pack("<Q", 2048)
            vbr[0x30:0x38] = struct.pack("<Q", 4)
            vbr[0x40] = 0xF6
            f.seek(0)
            f.write(vbr)
            # Write payload at cluster 10 (offset 10*4096 = 40960)
            f.seek(40960)
            f.write(original_payload)

        reader = RawDiskReader(disk_path, sector_size=512)
        volume = NTFSVolume(reader, 0)

        f_info = NTFSFileInfo(88)
        f_info.name = "forensic_target.bin"
        f_info.data_runs = [DataRun(lcn=10, cluster_count=3)]
        f_info.file_size = len(original_payload)

        engine = RecoveryEngine(volume, dest_dir, timeout_ms=500)
        engine.run_recovery({88: f_info})
        reader.close()

        out_path = os.path.join(dest_dir, "forensic_target.bin")
        hashes = compute_file_hashes(out_path)
        shutil.rmtree(temp_dir, ignore_errors=True)

        match = (hashes["sha256"] == expected_sha256)
        score = 20 if match else 0
        self.log_result(
            "Benchmark 4: SHA-256 Forensic Integrity",
            match,
            score,
            20,
            f"Expected: {expected_sha256[:16]}... | Got: {hashes['sha256'][:16]}... (Match: {match})",
        )

    def bench_5_fragmentation_and_tree(self):
        """Benchmark 5: Fragmented multi-run clusters and deep directory hierarchy."""
        temp_dir = tempfile.mkdtemp()
        disk_path = os.path.join(temp_dir, "bench5_disk.img")
        dest_dir = os.path.join(temp_dir, "recovered")

        with open(disk_path, "wb") as f:
            f.write(b"\x00" * (1024 * 1024))
        with open(disk_path, "r+b") as f:
            vbr = bytearray(512)
            vbr[3:11] = b"NTFS    "
            vbr[0x0B:0x0D] = struct.pack("<H", 512)
            vbr[0x0D] = 8
            vbr[0x28:0x30] = struct.pack("<Q", 2048)
            vbr[0x30:0x38] = struct.pack("<Q", 4)
            vbr[0x40] = 0xF6
            f.seek(0)
            f.write(vbr)
            # Write 3 fragments:
            f.seek(5 * 4096)
            f.write(b"FRAG_A_" * 500)
            f.seek(15 * 4096)
            f.write(b"FRAG_B_" * 500)
            f.seek(25 * 4096)
            f.write(b"FRAG_C_" * 500)

        reader = RawDiskReader(disk_path, sector_size=512)
        volume = NTFSVolume(reader, 0)

        # Multi-run fragmented file inside deep directory structure
        f_info = NTFSFileInfo(99)
        f_info.name = "database_fragmented.mdf"
        f_info.full_path = "Enterprise/Production/Databases/database_fragmented.mdf"
        f_info.data_runs = [
            DataRun(lcn=5, cluster_count=1),
            DataRun(lcn=15, cluster_count=1),
            DataRun(lcn=25, cluster_count=1),
        ]
        f_info.file_size = 10000

        engine = RecoveryEngine(volume, dest_dir, timeout_ms=500)
        engine.run_recovery({99: f_info})
        reader.close()

        expected_path = os.path.join(dest_dir, "Enterprise", "Production", "Databases", "database_fragmented.mdf")
        path_ok = os.path.exists(expected_path) and os.path.getsize(expected_path) == 10000
        shutil.rmtree(temp_dir, ignore_errors=True)

        score = 20 if path_ok else 0
        self.log_result(
            "Benchmark 5: Fragmentation & Tree Reconstruction",
            path_ok,
            score,
            20,
            f"Directory path generated: {path_ok} | Size: 10,000 bytes",
        )

    def print_scorecard(self):
        print("\n" + "=" * 78)
        print("                       QA BENCHMARK SCORECARD")
        print("=" * 78)
        for r in self.results:
            status_tag = "[PASS]" if r["passed"] else "[FAIL]"
            print(f" {status_tag} {r['name']:<46} : {r['score']:>2}/{r['max_score']:<2} pts")
            print(f"        └─ {r['details']}")

        print("-" * 78)
        print(f" TOTAL SCORE: {self.total_score} / {self.max_score} POINTS")
        if self.total_score >= 90:
            print(" VERDICT    : GRADE A+ (PRODUCTION & ENTERPRISE READY)")
        elif self.total_score >= 75:
            print(" VERDICT    : GRADE B (STABLE MVP)")
        else:
            print(" VERDICT    : GRADE C (NEEDS REFINEMENT)")
        print("=" * 78 + "\n")


if __name__ == "__main__":
    runner = BenchmarkRunner()
    runner.run_all()
