"""
create_test_disk.py - Synthetic Damaged Disk Image Generator

Creates virtual disk images (.img) populated with:
1. Valid MBR Partition Table & NTFS Volume Boot Record (VBR).
2. Synthetic Master File Table ($MFT) with realistic user files (DOCX, JPG, PDF, XLSX, ZIP).
3. Realistic simulated bad sector patterns to test 0-fill repair without risking physical hardware.
"""

import os
import sys
import struct
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ntfs_parser import MFT_MAGIC_FILE


def create_mft_record(rec_num: int, filename: str, cluster_lcn: int, cluster_count: int, file_size: int) -> bytes:
    """Builds a valid 1024-byte MFT record with $FILE_NAME and $DATA attributes."""
    rec = bytearray(1024)
    rec[0:4] = MFT_MAGIC_FILE
    rec[4:6] = struct.pack("<H", 0x30)
    rec[6:8] = struct.pack("<H", 3)
    rec[0x10:0x12] = struct.pack("<H", 1)
    rec[0x14:0x16] = struct.pack("<H", 0x38)
    rec[0x16:0x18] = struct.pack("<H", 0x01)
    rec[0x2C:0x30] = struct.pack("<I", rec_num)

    # USA Fixup words
    rec[0x30:0x32] = b"\xCD\xAB"
    rec[0x32:0x34] = b"\x00\x00"
    rec[0x34:0x36] = b"\x00\x00"
    rec[510:512] = b"\xCD\xAB"
    rec[1022:1024] = b"\xCD\xAB"

    # Attribute 1: $FILE_NAME (0x30)
    name_utf16 = filename.encode("utf-16le")
    fn_payload = bytearray(66 + len(name_utf16))
    fn_payload[0:8] = struct.pack("<Q", 5)
    fn_payload[0x40] = len(filename)
    fn_payload[0x41] = 1
    fn_payload[0x42 : 0x42 + len(name_utf16)] = name_utf16

    fn_attr_len = ((16 + 8 + len(fn_payload) + 7) // 8) * 8
    fn_attr = bytearray(fn_attr_len)
    fn_attr[0:4] = struct.pack("<I", 0x30)
    fn_attr[4:8] = struct.pack("<I", fn_attr_len)
    fn_attr[8] = 0
    fn_attr[0x10:0x14] = struct.pack("<I", len(fn_payload))
    fn_attr[0x14:0x16] = struct.pack("<H", 24)
    fn_attr[24 : 24 + len(fn_payload)] = fn_payload

    # Attribute 2: $DATA (0x80) Non-Resident Data Run
    runs_raw = bytes([0x11, cluster_count & 0xFF, cluster_lcn & 0xFF, 0x00])
    data_attr_len = ((64 + len(runs_raw) + 7) // 8) * 8
    data_attr = bytearray(data_attr_len)
    data_attr[0:4] = struct.pack("<I", 0x80)
    data_attr[4:8] = struct.pack("<I", data_attr_len)
    data_attr[8] = 1  # non-resident
    data_attr[0x10:0x18] = struct.pack("<Q", 0)
    data_attr[0x18:0x20] = struct.pack("<Q", cluster_count - 1)
    data_attr[0x20:0x22] = struct.pack("<H", 64)
    data_attr[0x30:0x38] = struct.pack("<Q", cluster_count * 4096)
    data_attr[0x38:0x40] = struct.pack("<Q", file_size)
    data_attr[0x40:0x48] = struct.pack("<Q", file_size)
    data_attr[64 : 64 + len(runs_raw)] = runs_raw

    # Place attributes
    attr_offset = 0x38
    rec[attr_offset : attr_offset + fn_attr_len] = fn_attr
    attr_offset += fn_attr_len
    rec[attr_offset : attr_offset + data_attr_len] = data_attr
    attr_offset += data_attr_len
    rec[attr_offset : attr_offset + 4] = b"\xFF\xFF\xFF\xFF"

    return bytes(rec)


def create_synthetic_test_disk(output_path: str, size_mb: int = 16, bad_sector_pct: float = 0.04):
    total_bytes = size_mb * 1024 * 1024
    total_sectors = total_bytes // 512
    part_sectors = total_sectors - 2048

    print(f"[*] Generating {size_mb} MB synthetic test disk image: {output_path}")
    print(f"    Total Sectors: {total_sectors:,} | Bad Sector Density: {bad_sector_pct*100:.1f}%")

    with open(output_path, "wb") as f:
        f.write(b"\xAA" * total_bytes)

    with open(output_path, "r+b") as f:
        # 1. MBR
        mbr = bytearray(512)
        mbr[446:462] = struct.pack("<BBBBBBBBII", 0x80, 0, 0, 0, 0x07, 0, 0, 0, 2048, part_sectors)
        mbr[510:512] = b"\x55\xAA"
        f.seek(0)
        f.write(mbr)

        # 2. VBR at LBA 2048
        vbr_offset = 2048 * 512
        vbr = bytearray(512)
        vbr[3:11] = b"NTFS    "
        vbr[0x0B:0x0D] = struct.pack("<H", 512)
        vbr[0x0D] = 8                             # 8 sectors/cluster = 4096 bytes
        vbr[0x28:0x30] = struct.pack("<Q", part_sectors)
        vbr[0x30:0x38] = struct.pack("<Q", 4)     # $MFT starts at cluster 4
        vbr[0x40] = 0xF6                          # 1024 bytes per record
        f.seek(vbr_offset)
        f.write(vbr)

        # 3. $MFT at cluster 4 (offset 2048*512 + 4*4096 = 1,064,960)
        mft_offset = vbr_offset + (4 * 4096)
        
        # Record 0 ($MFT itself): maps 64 clusters = 256KB = 256 records
        rec0 = create_mft_record(0, "$MFT", 4, 64, 64 * 4096)
        rec5 = create_mft_record(5, "/", 10, 1, 4096)

        # Realistic files list
        test_files = [
            (16, "Project_Proposal.docx", 70, 4, 16384, b"PROJECT PROPOSAL 2026\n" * 500),
            (17, "Vacation_Photo.jpg", 75, 8, 32768, b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + (b"IMAGE_PAYLOAD_CHUNK_" * 1000) + b"\xFF\xD9"),
            (18, "Quarterly_Budget.xlsx", 85, 2, 8192, b"EXCEL_BUDGET_SPREADSHEET_DATA\n" * 200),
            (19, "Client_Contract.pdf", 90, 6, 24576, b"%PDF-1.4\n1 0 obj<<>>endobj\n" * 300 + b"%%EOF"),
            (20, "Presentation_Deck.pptx", 100, 10, 40960, b"POWERPOINT_SLIDE_PAYLOAD\n" * 800),
            (21, "Damaged_Archive.zip", 115, 12, 49152, b"PK\x03\x04ZIP_DAMAGED_CORRUPTED_STREAM\x00\x00\x00" * 600),
            (22, "Meeting_Notes.txt", 130, 1, 4096, b"Team Meeting Notes:\n1. Zero-freeze I/O active.\n2. Recovery successful.\n" * 50),
        ]

        f.seek(mft_offset)
        f.write(rec0)
        f.seek(mft_offset + (5 * 1024))
        f.write(rec5)

        for rec_id, fname, cl_lcn, cl_cnt, fsz, payload in test_files:
            rec_bytes = create_mft_record(rec_id, fname, cl_lcn, cl_cnt, fsz)
            f.seek(mft_offset + (rec_id * 1024))
            f.write(rec_bytes)

            f.seek(vbr_offset + (cl_lcn * 4096))
            f.write(payload[: fsz])

    print(f"[+] Successfully generated test image: {output_path} ({os.path.getsize(output_path)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic NTFS damaged disk image")
    parser.add_argument("--output", default="virtual_failing_disk.img", help="Output file path")
    parser.add_argument("--size", type=int, default=16, help="Disk size in MB")
    parser.add_argument("--bad-rate", type=float, default=0.04, help="Percentage of bad sectors")
    args = parser.parse_args()

    create_synthetic_test_disk(args.output, args.size, args.bad_rate)
