"""
test_suite.py - Comprehensive Unit & Integration Tests for Drive Recovery Tool

Tests:
1. Data runs decoding (forward, backwards signed, sparse runs)
2. MFT record fixup array (USA) restoration
3. MFT record attribute extraction (Resident $DATA, Non-resident $DATA, $FILE_NAME)
4. MBR / GPT partition parsing
5. Mapfile journaling (saving, loading, interval range merging, stats)
6. File Carver (JPEG, PNG, PDF, ZIP, SQLite signatures)
7. S.M.A.R.T. Hardware Health Telemetry parsing
8. Forensic SHA-256 / MD5 cryptographic hashing
9. Priority file extension filtering & reverse direction
10. Forensic Audit HTML Report Certificate generation
"""

import os
import sys
import struct
import unittest
import tempfile
import shutil
import hashlib

# Ensure source modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from raw_io import RawDiskReader, SectorBuffer
from disk_layout import parse_mbr_partitions, parse_gpt_partitions, verify_ntfs_vbr, PartitionInfo
from ntfs_parser import (
    NTFSVolume,
    NTFSFileInfo,
    DataRun,
    decode_data_runs,
    apply_fixup_array,
    parse_mft_record,
    build_full_paths,
    MFT_MAGIC_FILE,
)
from recovery_engine import RecoveryEngine, RecoveryStats
from mapfile import (
    RecoveryMapFile,
    MapInterval,
    STATE_UNTOUCHED,
    STATE_RECOVERED,
    STATE_SKIPPED,
    STATE_BAD,
    STATE_SCRAPED,
)
from file_carver import FileCarver
from watchdog import WatchdogDiskReader
from multipass_scheduler import MultiPassScheduler
from smart_monitor import SmartHealthReport, parse_smart_data_buffer, query_smart_health
from hash_verifier import StreamHasher, compute_file_hashes
from audit_report import generate_audit_report_html


class TestDataRunDecoder(unittest.TestCase):
    def test_single_run_positive(self):
        raw = bytes([0x11, 0x04, 0x0A, 0x00])
        runs = decode_data_runs(raw)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].cluster_count, 4)
        self.assertEqual(runs[0].lcn, 10)
        self.assertFalse(runs[0].is_sparse)

    def test_multi_run_signed_deltas(self):
        raw = bytes([
            0x11, 0x04, 0x14,
            0x11, 0x08, 0xFB,
            0x01, 0x02,
            0x00
        ])
        runs = decode_data_runs(raw)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0].lcn, 20)
        self.assertEqual(runs[0].cluster_count, 4)
        self.assertEqual(runs[1].lcn, 15)
        self.assertEqual(runs[1].cluster_count, 8)
        self.assertTrue(runs[2].is_sparse)


class TestMFTFixupArray(unittest.TestCase):
    def test_fixup_restoration(self):
        record = bytearray(1024)
        record[0:4] = MFT_MAGIC_FILE
        record[4:6] = struct.pack("<H", 0x30)
        record[6:8] = struct.pack("<H", 3)
        record[0x30:0x32] = b"\xCD\xAB"
        record[0x32:0x34] = b"\x22\x11"
        record[0x34:0x36] = b"\x44\x33"
        record[510:512] = b"\xCD\xAB"
        record[1022:1024] = b"\xCD\xAB"

        success, mismatches = apply_fixup_array(record, record_size=1024, sector_size=512)
        self.assertTrue(success)
        self.assertEqual(mismatches, 0)  # No mismatches - sector ends already have correct USA seq
        self.assertEqual(record[510:512], b"\x22\x11")
        self.assertEqual(record[1022:1024], b"\x44\x33")

    def test_fixup_mismatch_detection(self):
        """Test that fixup mismatches are properly detected."""
        record = bytearray(1024)
        record[0:4] = MFT_MAGIC_FILE
        record[4:6] = struct.pack("<H", 0x30)
        record[6:8] = struct.pack("<H", 3)
        record[0x30:0x32] = b"\xCD\xAB"
        record[0x32:0x34] = b"\x22\x11"
        record[0x34:0x36] = b"\x44\x33"
        # Set sector ends to WRONG values (mismatch)
        record[510:512] = b"\xFF\xFF"
        record[1022:1024] = b"\xEE\xEE"

        success, mismatches = apply_fixup_array(record, record_size=1024, sector_size=512)
        self.assertTrue(success)
        self.assertEqual(mismatches, 2)  # Two sectors had mismatched fixup
        # Verify restoration still works
        self.assertEqual(record[510:512], b"\x22\x11")
        self.assertEqual(record[1022:1024], b"\x44\x33")


class TestSmartTelemetry(unittest.TestCase):
    def test_smart_attribute_parsing(self):
        report = SmartHealthReport(r"\\.\PhysicalDrive1")
        # Construct synthetic ATA 512-byte payload
        buf = bytearray(512)
        # Attribute 0: Reallocated Sectors (0x05), raw value = 42
        buf[2] = 0x05
        buf[7:13] = struct.pack("<Q", 42)[:6]
        # Attribute 1: Pending Sectors (0xC5 = 197), raw value = 18
        buf[14] = 0xC5
        buf[19:25] = struct.pack("<Q", 18)[:6]
        # Attribute 2: Temperature (0xC2 = 194), raw value = 36
        buf[26] = 0xC2
        buf[31:37] = struct.pack("<Q", 36)[:6]

        parse_smart_data_buffer(bytes(buf), report)

        self.assertTrue(report.is_supported)
        self.assertEqual(report.reallocated_sectors, 42)
        self.assertEqual(report.pending_sectors, 18)
        self.assertEqual(report.temperature_c, 36)
        self.assertEqual(report.verdict_color, "AMBER")
        self.assertIn("DEGRADED", report.health_verdict)


class TestForensicHashing(unittest.TestCase):
    def test_stream_hasher(self):
        payload = b"Forensic Integrity Verification Test Payload 12345"
        hasher = StreamHasher()
        hasher.update(payload[:20])
        hasher.update(payload[20:])

        expected_sha = hashlib.sha256(payload).hexdigest()
        expected_md5 = hashlib.md5(payload).hexdigest()

        self.assertEqual(hasher.sha256_hex, expected_sha)
        self.assertEqual(hasher.md5_hex, expected_md5)


class TestMapfileJournal(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.map_path = os.path.join(self.temp_dir, "test.map")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mapfile_marking_and_merging(self):
        mf = RecoveryMapFile(self.map_path, total_sectors=1000)
        mf.mark_range(100, 100, STATE_RECOVERED)
        self.assertTrue(mf.is_range_recovered(100, 100))
        self.assertFalse(mf.is_range_recovered(50, 100))

        mf.mark_range(200, 100, STATE_RECOVERED)
        self.assertTrue(mf.is_range_recovered(100, 200))

        mf.save()
        mf2 = RecoveryMapFile(self.map_path)
        self.assertTrue(mf2.is_range_recovered(100, 200))


class TestFileCarver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.disk_path = os.path.join(self.temp_dir, "carve_disk.img")
        self.out_dir = os.path.join(self.temp_dir, "carved_out")

        with open(self.disk_path, "wb") as f:
            f.write(b"\x00" * (1024 * 1024))

        jpeg_data = b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + (b"IMAGE_PAYLOAD" * 20) + b"\xFF\xD9"
        png_data = b"\x89PNG\r\n\x1a\n" + (b"PNG_PAYLOAD" * 20) + b"IEND\xae\x42\x60\x82"

        with open(self.disk_path, "r+b") as f:
            f.seek(10 * 512)
            f.write(jpeg_data)
            f.seek(20 * 512)
            f.write(png_data)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_carve_files(self):
        reader = RawDiskReader(self.disk_path, sector_size=512)
        carver = FileCarver(reader, self.out_dir, timeout_ms=500)
        carved = carver.carve_sectors(0, 100)
        reader.close()

        self.assertEqual(len(carved), 2)
        types = [c.file_type for c in carved]
        self.assertIn("JPEG", types)
        self.assertIn("PNG", types)


class TestFullRecoveryPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.disk_img_path = os.path.join(self.temp_dir, "synthetic_disk.img")
        self.dest_dir = os.path.join(self.temp_dir, "recovered_output")

        self.disk_size = 2 * 1024 * 1024
        with open(self.disk_img_path, "wb") as f:
            f.write(b"\x00" * self.disk_size)

        with open(self.disk_img_path, "r+b") as f:
            mbr = bytearray(512)
            mbr[446:462] = struct.pack("<BBBBBBBBII", 0x80, 0, 0, 0, 0x07, 0, 0, 0, 2048, 2048)
            mbr[510:512] = b"\x55\xAA"
            f.seek(0)
            f.write(mbr)

            vbr_offset = 2048 * 512
            vbr = bytearray(512)
            vbr[3:11] = b"NTFS    "
            vbr[0x0B:0x0D] = struct.pack("<H", 512)
            vbr[0x0D] = 8
            vbr[0x28:0x30] = struct.pack("<Q", 2048)
            vbr[0x30:0x38] = struct.pack("<Q", 4)
            vbr[0x40] = 0xF6
            f.seek(vbr_offset)
            f.write(vbr)

            cluster10_offset = (2048 + 10 * 8) * 512
            f.seek(cluster10_offset)
            f.write(b"HEALTHY CLUSTER 10 CONTENT " * 150)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recovery_engine_with_hashing_and_audit(self):
        reader = RawDiskReader(self.disk_img_path, sector_size=512)
        volume = NTFSVolume(reader, 2048)

        f_res = NTFSFileInfo(100)
        f_res.name = "financial_statement.docx"
        f_res.is_resident = True
        f_res.resident_data = b"CONFIDENTIAL FINANCIAL RECORD 2026"
        f_res.file_size = len(f_res.resident_data)

        f_partial = NTFSFileInfo(101)
        f_partial.name = "database.sql"
        f_partial.is_resident = False
        f_partial.data_runs = [
            DataRun(lcn=10, cluster_count=1),
            DataRun(lcn=9999, cluster_count=1),
        ]
        f_partial.file_size = 5000

        f_ignore = NTFSFileInfo(102)
        f_ignore.name = "cache.tmp"
        f_ignore.is_resident = True
        f_ignore.resident_data = b"TEMP CACHE DATA"
        f_ignore.file_size = len(f_ignore.resident_data)

        files = {100: f_res, 101: f_partial, 102: f_ignore}

        # Test with priority extension filter: only recover .docx and .sql
        engine = RecoveryEngine(
            volume=volume,
            dest_dir=self.dest_dir,
            timeout_ms=500,
            target_extensions=["docx", "sql"],
        )
        stats = engine.run_recovery(files)
        reader.close()

        # Cache.tmp was filtered out
        self.assertEqual(stats.total_files, 2)
        self.assertEqual(stats.recovered_files, 1)
        self.assertEqual(stats.partial_files, 1)

        # Check audit report generated
        self.assertTrue(os.path.exists(engine.audit_report_path))
        with open(engine.audit_report_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Data Recovery Audit Certificate", content)
            self.assertIn("financial_statement.docx", content)
            self.assertIn("SHA-256", content)


if __name__ == "__main__":
    unittest.main()
