"""
disk_layout.py - Partition Table Parser (MBR & GPT) and NTFS Partition Scanner

Reads raw disk LBA 0 and LBA 1+ to discover all partitions, identify GUIDs and
MBR types, and verify NTFS Volume Boot Records (VBR).
"""

import struct
import uuid
import zlib
from typing import List, Dict, Optional, Any
from raw_io import RawDiskReader

# Microsoft Basic Data Partition GUID: EBD0A0A2-B9E5-4433-87C0-68B6B72699C7
GUID_BASIC_DATA = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
GUID_EMPTY = uuid.UUID("00000000-0000-0000-0000-000000000000")


class PartitionInfo:
    """Represents a discovered disk partition."""
    def __init__(
        self,
        index: int,
        partition_type: str,
        start_lba: int,
        sector_count: int,
        sector_size: int = 512,
        is_ntfs: bool = False,
        name: str = "",
        guid: Optional[str] = None,
    ):
        self.index = index
        self.partition_type = partition_type  # 'MBR' or 'GPT'
        self.start_lba = start_lba
        self.sector_count = sector_count
        self.sector_size = sector_size
        self.is_ntfs = is_ntfs
        self.name = name
        self.guid = guid
        self.size_bytes = sector_count * sector_size
        self.size_gb = self.size_bytes / (1024**3)

    def __repr__(self) -> str:
        fs = "NTFS" if self.is_ntfs else "Unknown/Other"
        return (
            f"<Partition #{self.index} [{self.partition_type}] "
            f"LBA: {self.start_lba:,} - {self.start_lba + self.sector_count - 1:,} "
            f"({self.size_gb:.2f} GB) FS: {fs} Name: '{self.name}'>"
        )


def parse_mbr_partitions(reader: RawDiskReader) -> List[PartitionInfo]:
    """
    Parses MBR partition table at LBA 0.
    """
    sector0 = reader.read_sectors(0, 1)
    if not sector0 or len(sector0) < 512:
        return []

    # Check 0x55AA signature
    if sector0[510:512] != b"\x55\xAA":
        return []

    partitions = []
    # 4 partition entries starting at offset 446 (0x1BE)
    for i in range(4):
        offset = 446 + (i * 16)
        entry = sector0[offset : offset + 16]
        if len(entry) < 16:
            continue

        boot_flag, _, _, _, p_type, _, _, _, start_lba, num_sectors = struct.unpack(
            "<BBBBBBBBII", entry
        )

        if p_type == 0x00 or num_sectors == 0:
            continue

        if p_type == 0xEE:
            # GPT Protective MBR indicator
            return parse_gpt_partitions(reader)

        # Check if NTFS (0x07 is IFS/NTFS/exFAT)
        is_ntfs = False
        if p_type == 0x07:
            is_ntfs = verify_ntfs_vbr(reader, start_lba)

        p = PartitionInfo(
            index=i + 1,
            partition_type="MBR",
            start_lba=start_lba,
            sector_count=num_sectors,
            sector_size=reader.sector_size,
            is_ntfs=is_ntfs,
            name=f"MBR Partition {i+1} (Type 0x{p_type:02X})",
        )
        partitions.append(p)

    return partitions


def parse_gpt_partitions(reader: RawDiskReader) -> List[PartitionInfo]:
    """
    Parses GUID Partition Table starting at LBA 1.
    Validates GPT header CRC32 and partition entry array CRC32.
    """
    gpt_header_data = reader.read_sectors(1, 1)
    if not gpt_header_data or len(gpt_header_data) < 512:
        return []

    # Verify signature "EFI PART"
    if gpt_header_data[0:8] != b"EFI PART":
        return []

    (
        sig,
        revision,
        header_size,
        crc32,
        _,
        current_lba,
        backup_lba,
        first_usable_lba,
        last_usable_lba,
        disk_guid_raw,
        entries_lba,
        num_entries,
        entry_size,
    ) = struct.unpack("<8sIIIIQQQQ16sQII", gpt_header_data[:88])

    # Validate GPT header CRC32 (header with CRC32 field zeroed)
    header_for_crc = bytearray(gpt_header_data[:header_size])
    # Zero out the CRC32 field at offset 0x10 (16)
    header_for_crc[0x10:0x14] = b"\x00\x00\x00\x00"
    computed_crc = zlib.crc32(header_for_crc) & 0xFFFFFFFF
    if computed_crc != crc32:
        # Header CRC mismatch - GPT may be corrupted
        return []

    if num_entries == 0 or entry_size < 128:
        return []

    # Calculate how many sectors we need to read for all partition entries
    total_entry_bytes = num_entries * entry_size
    sectors_to_read = (total_entry_bytes + reader.sector_size - 1) // reader.sector_size

    entries_data = reader.read_sectors(entries_lba, sectors_to_read)
    if not entries_data:
        return []

    # Validate partition entry array CRC32
    entries_for_crc = entries_data[:total_entry_bytes]
    entries_crc = zlib.crc32(entries_for_crc) & 0xFFFFFFFF
    
    # Read backup GPT header to get the expected partition array CRC32
    # Backup header is at backup_lba
    backup_header_data = reader.read_sectors(backup_lba, 1)
    expected_entries_crc = 0
    if backup_header_data and len(backup_header_data) >= 88:
        if backup_header_data[0:8] == b"EFI PART":
            _, _, _, _, _, _, _, _, _, _, _, _, backup_entry_size = struct.unpack("<8sIIIIQQQQ16sQII", backup_header_data[:88])
            # The partition entry array CRC32 is at offset 0x58 (88) in the backup header
            if len(backup_header_data) >= 92:
                expected_entries_crc = struct.unpack("<I", backup_header_data[88:92])[0]
    
    if expected_entries_crc and entries_crc != expected_entries_crc:
        # Partition array CRC mismatch - entries may be corrupted
        return []

    partitions = []
    valid_idx = 1
    for i in range(num_entries):
        offset = i * entry_size
        if offset + entry_size > len(entries_data):
            break

        entry_bytes = entries_data[offset : offset + entry_size]
        type_guid_raw = entry_bytes[0:16]
        part_guid_raw = entry_bytes[16:32]

        if type_guid_raw == b"\x00" * 16:
            continue

        # Convert raw GUID bytes to UUID
        type_guid = uuid.UUID(bytes_le=type_guid_raw)
        part_guid = uuid.UUID(bytes_le=part_guid_raw)

        start_lba, end_lba, attributes = struct.unpack("<QQQ", entry_bytes[32:56])
        name_bytes = entry_bytes[56:128]
        try:
            name = name_bytes.decode("utf-16le").rstrip("\x00")
        except Exception:
            name = ""

        sector_count = (end_lba - start_lba) + 1
        if sector_count <= 0:
            continue

        # Check for NTFS filesystem
        is_ntfs = verify_ntfs_vbr(reader, start_lba)

        p = PartitionInfo(
            index=valid_idx,
            partition_type="GPT",
            start_lba=start_lba,
            sector_count=sector_count,
            sector_size=reader.sector_size,
            is_ntfs=is_ntfs,
            name=name if name else f"GPT Partition {valid_idx}",
            guid=str(part_guid),
        )
        partitions.append(p)
        valid_idx += 1

    return partitions


def verify_ntfs_vbr(reader: RawDiskReader, start_lba: int) -> bool:
    """
    Reads the Volume Boot Record at start_lba and checks if the OEM ID is 'NTFS    '.
    """
    vbr_data = reader.read_sectors(start_lba, 1)
    if not vbr_data or len(vbr_data) < 512:
        return False

    # NTFS OEM ID is at offset 3, 8 bytes
    oem_id = vbr_data[3:11]
    return oem_id == b"NTFS    "


def scan_partitions(reader: RawDiskReader) -> List[PartitionInfo]:
    """
    Comprehensive scanner: checks MBR/GPT, and if none found, tests if LBA 0 itself is a raw NTFS volume (VBR).
    """
    # 1. Try standard MBR / GPT parsing
    parts = parse_mbr_partitions(reader)
    if parts:
        return parts

    # 2. Check if LBA 0 itself is an unpartitioned raw NTFS volume (e.g. Volume device or direct image)
    if verify_ntfs_vbr(reader, 0):
        # Read total sectors from VBR
        vbr_data = reader.read_sectors(0, 1)
        if vbr_data and len(vbr_data) >= 512:
            total_sectors = struct.unpack("<Q", vbr_data[0x28:0x30])[0]
            if total_sectors == 0:
                total_sectors = (reader.disk_size_bytes // reader.sector_size) if reader.disk_size_bytes > 0 else 204800
            return [
                PartitionInfo(
                    index=1,
                    partition_type="RAW/SUPERFLOPPY",
                    start_lba=0,
                    sector_count=total_sectors,
                    sector_size=reader.sector_size,
                    is_ntfs=True,
                    name="Raw NTFS Volume",
                )
            ]

    # 3. If no partitions found at LBA 0, scan common partition offsets (e.g., LBA 2048, 63, 1024)
    common_offsets = [2048, 63, 1024, 4096]
    for offset in common_offsets:
        if verify_ntfs_vbr(reader, offset):
            vbr_data = reader.read_sectors(offset, 1)
            if vbr_data:
                total_sectors = struct.unpack("<Q", vbr_data[0x28:0x30])[0]
                return [
                    PartitionInfo(
                        index=1,
                        partition_type="DISCOVERED",
                        start_lba=offset,
                        sector_count=total_sectors if total_sectors > 0 else 204800,
                        sector_size=reader.sector_size,
                        is_ntfs=True,
                        name=f"Discovered NTFS Volume @ LBA {offset}",
                    )
                ]

    return []
