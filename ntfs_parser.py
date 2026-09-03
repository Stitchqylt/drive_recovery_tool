"""
ntfs_parser.py - NTFS Boot Sector & Master File Table ($MFT) Parser

Parses NTFS Volume Boot Record (VBR), MFT records, Fixup Sequences (USA),
Standard Information, File Name attributes, and resident / non-resident Data Runs
to map files to their physical clusters on disk.
"""

import struct
from typing import List, Dict, Tuple, Optional, Any
from raw_io import RawDiskReader

# MFT Record Signatures
MFT_MAGIC_FILE = b"FILE"
MFT_MAGIC_BAAD = b"BAAD"

# Attribute Types
ATTR_STANDARD_INFORMATION = 0x10
ATTR_ATTRIBUTE_LIST = 0x20
ATTR_FILE_NAME = 0x30
ATTR_OBJECT_ID = 0x40
ATTR_SECURITY_DESCRIPTOR = 0x50
ATTR_VOLUME_NAME = 0x60
ATTR_VOLUME_INFORMATION = 0x70
ATTR_DATA = 0x80
ATTR_INDEX_ROOT = 0x90
ATTR_INDEX_ALLOCATION = 0xA0
ATTR_BITMAP = 0xB0
ATTR_REPARSE_POINT = 0xC0
ATTR_END = 0xFFFFFFFF

# File Name Namespaces
NS_POSIX = 0
NS_WIN32 = 1
NS_DOS = 2
NS_WIN32_DOS = 3


class DataRun:
    """Represents a contiguous cluster allocation run on disk."""
    def __init__(self, lcn: Optional[int], cluster_count: int, is_sparse: bool = False):
        self.lcn = lcn  # Logical Cluster Number (None if sparse)
        self.cluster_count = cluster_count
        self.is_sparse = is_sparse

    def __repr__(self) -> str:
        if self.is_sparse:
            return f"<DataRun Sparse {self.cluster_count} clusters>"
        return f"<DataRun LCN {self.lcn} count {self.cluster_count}>"


class NTFSFileInfo:
    """Represents a file or directory discovered in the MFT."""
    def __init__(self, record_number: int):
        self.record_number = record_number
        self.sequence_number = 0
        self.parent_record_number = 0
        self.name = ""
        self.is_directory = False
        self.is_in_use = True
        self.file_size = 0
        self.allocated_size = 0
        self.is_resident = False
        self.resident_data: Optional[bytes] = None
        self.data_runs: List[DataRun] = []
        self.full_path = ""
        self.attribute_list: Optional[bytes] = None

    def __repr__(self) -> str:
        kind = "DIR" if self.is_directory else "FILE"
        return (
            f"<NTFSFileInfo #{self.record_number} [{kind}] '{self.full_path or self.name}' "
            f"Size: {self.file_size:,} bytes Runs: {len(self.data_runs)}>"
        )


class NTFSVolume:
    """
    Encapsulates an NTFS volume, its boot record parameters, and MFT access.
    """
    def __init__(self, reader: RawDiskReader, partition_start_lba: int):
        self.reader = reader
        self.partition_start_lba = partition_start_lba

        # Boot sector parameters
        self.bytes_per_sector = 512
        self.sectors_per_cluster = 8
        self.bytes_per_cluster = 4096
        self.total_sectors = 0
        self.mft_start_lcn = 0
        self.mft_mirror_lcn = 0
        self.mft_record_size = 1024
        self.clusters_per_mft_record = 0

        self._load_boot_sector()

    def _load_boot_sector(self):
        """Reads and validates the NTFS Volume Boot Record (VBR)."""
        vbr_data = self.reader.read_sectors(self.partition_start_lba, 1)
        if not vbr_data or len(vbr_data) < 512:
            raise ValueError(f"Failed to read VBR at LBA {self.partition_start_lba}")

        if vbr_data[3:11] != b"NTFS    ":
            raise ValueError(f"Invalid NTFS signature '{vbr_data[3:11]}' at LBA {self.partition_start_lba}")

        self.bytes_per_sector = struct.unpack("<H", vbr_data[0x0B:0x0D])[0]
        self.sectors_per_cluster = vbr_data[0x0D]
        self.bytes_per_cluster = self.bytes_per_sector * self.sectors_per_cluster
        self.total_sectors = struct.unpack("<Q", vbr_data[0x28:0x30])[0]
        self.mft_start_lcn = struct.unpack("<Q", vbr_data[0x30:0x38])[0]
        self.mft_mirror_lcn = struct.unpack("<Q", vbr_data[0x38:0x40])[0]

        # MFT record size calculation (offset 0x40 is signed int8)
        raw_mft_clusters = struct.unpack("<b", vbr_data[0x40:0x41])[0]
        if raw_mft_clusters > 0:
            self.mft_record_size = raw_mft_clusters * self.bytes_per_cluster
        else:
            self.mft_record_size = 2 ** abs(raw_mft_clusters)

    def lcn_to_sector(self, lcn: int) -> int:
        """Converts a Volume Logical Cluster Number to Physical Sector LBA."""
        return self.partition_start_lba + (lcn * self.sectors_per_cluster)

    def read_cluster(self, lcn: int, timeout_ms: Optional[int] = None) -> Optional[bytes]:
        """Reads a single cluster by its LCN."""
        sector = self.lcn_to_sector(lcn)
        return self.reader.read_sectors(sector, self.sectors_per_cluster, timeout_ms)


def apply_fixup_array(record_data: bytearray, record_size: int = 1024, sector_size: int = 512) -> Tuple[bool, int]:
    """
    Validates and restores the Update Sequence Array (USA / Fixup) on an MFT record.
    Modifies record_data in-place.
    Returns (success, mismatch_count).
    """
    if len(record_data) < record_size:
        return False, 0

    if record_data[0:4] != MFT_MAGIC_FILE:
        return False, 0

    usa_offset = struct.unpack("<H", record_data[4:6])[0]
    usa_count = struct.unpack("<H", record_data[6:8])[0]

    if usa_offset + (usa_count * 2) > len(record_data):
        return False, 0

    # Extract update sequence number
    usa_seq_num = record_data[usa_offset : usa_offset + 2]
    num_sectors = record_size // sector_size

    if usa_count < num_sectors + 1:
        return False, 0

    mismatch_count = 0
    # Verify and replace sector ends
    for i in range(num_sectors):
        sector_end = (i + 1) * sector_size - 2
        current_seq = record_data[sector_end : sector_end + 2]
        if current_seq != usa_seq_num:
            # Fixup mismatch - sector may have partial write or corruption
            mismatch_count += 1
        # Restore original 2 bytes from USA array
        orig_val = record_data[usa_offset + 2 + (i * 2) : usa_offset + 4 + (i * 2)]
        record_data[sector_end : sector_end + 2] = orig_val

    return True, mismatch_count


def decode_data_runs(run_bytes: bytes) -> List[DataRun]:
    """
    Decodes NTFS data runs (variable-length signed delta cluster chains).
    """
    runs = []
    offset = 0
    current_lcn = 0

    while offset < len(run_bytes):
        header = run_bytes[offset]
        if header == 0:
            break
        offset += 1

        len_size = header & 0x0F
        offset_size = (header >> 4) & 0x0F

        if offset + len_size > len(run_bytes):
            break

        # Read cluster count (unsigned)
        count_bytes = run_bytes[offset : offset + len_size]
        offset += len_size
        cluster_count = int.from_bytes(count_bytes, byteorder="little", signed=False)

        if offset_size == 0:
            # Sparse run
            runs.append(DataRun(lcn=None, cluster_count=cluster_count, is_sparse=True))
        else:
            if offset + offset_size > len(run_bytes):
                break
            delta_bytes = run_bytes[offset : offset + offset_size]
            offset += offset_size
            cluster_offset_delta = int.from_bytes(delta_bytes, byteorder="little", signed=True)
            current_lcn += cluster_offset_delta
            runs.append(DataRun(lcn=current_lcn, cluster_count=cluster_count, is_sparse=False))

    return runs


def parse_attribute_list(attr_list_bytes: bytes) -> List[Tuple[int, int, int]]:
    """
    Parses $ATTRIBUTE_LIST entries.
    Returns list of (attribute_type, record_number, sequence_number).
    Each entry is 24 bytes minimum (can be larger with name).
    """
    entries = []
    offset = 0
    while offset + 24 <= len(attr_list_bytes):
        attr_type = struct.unpack("<I", attr_list_bytes[offset:offset+4])[0]
        record_length = struct.unpack("<H", attr_list_bytes[offset+4:offset+6])[0]
        if record_length < 24 or offset + record_length > len(attr_list_bytes):
            break
        # Offset 8-15: Starting VCN (8 bytes) - skip
        # Offset 16-23: Base file reference (8 bytes) - 48-bit record number + 16-bit sequence
        base_ref = struct.unpack("<Q", attr_list_bytes[offset+16:offset+24])[0]
        ref_record = base_ref & 0x0000FFFFFFFFFFFF
        ref_seq = (base_ref >> 48) & 0xFFFF
        entries.append((attr_type, ref_record, ref_seq))
        offset += record_length
    return entries


def read_mft_record_by_number(volume: NTFSVolume, record_number: int, record_size: int = 1024) -> Optional[bytes]:
    """Reads a specific MFT record by its record number."""
    sectors_per_record = record_size // volume.bytes_per_sector
    if sectors_per_record == 0:
        sectors_per_record = 1
    
    # Read Record 0 ($MFT) to get its data runs
    mft_sector0 = volume.lcn_to_sector(volume.mft_start_lcn)
    record0_bytes = volume.reader.read_sectors(mft_sector0, sectors_per_record)
    if not record0_bytes:
        return None
    
    record0 = parse_mft_record(record0_bytes, 0, record_size)
    if not record0 or not record0.data_runs:
        return None
    
    records_per_cluster = volume.bytes_per_cluster // record_size
    if records_per_cluster == 0:
        records_per_cluster = 1
    
    target_rec_idx = record_number
    
    for run in record0.data_runs:
        if run.is_sparse or run.lcn is None:
            target_rec_idx -= run.cluster_count * records_per_cluster
            continue
        
        for cluster_i in range(run.cluster_count):
            cluster_lcn = run.lcn + cluster_i
            cluster_data = volume.read_cluster(cluster_lcn)
            if not cluster_data:
                target_rec_idx -= records_per_cluster
                continue
            
            for r in range(records_per_cluster):
                if target_rec_idx == 0:
                    r_offset = r * record_size
                    if r_offset + record_size <= len(cluster_data):
                        return cluster_data[r_offset : r_offset + record_size]
                target_rec_idx -= 1
            
            if target_rec_idx < 0:
                break
        if target_rec_idx < 0:
            break
    
    return None


def resolve_attribute_list(volume: NTFSVolume, file_info: NTFSFileInfo, record_size: int = 1024) -> List[DataRun]:
    """
    Follows $ATTRIBUTE_LIST to collect all data runs from extension records.
    """
    if not file_info.attribute_list:
        return file_info.data_runs
    
    all_runs = list(file_info.data_runs)
    attr_list_entries = parse_attribute_list(file_info.attribute_list)
    
    for attr_type, ref_record, ref_seq in attr_list_entries:
        if attr_type != ATTR_DATA:
            continue
        
        # Read the referenced MFT record
        ext_record_bytes = read_mft_record_by_number(volume, ref_record, record_size)
        if not ext_record_bytes:
            continue
        
        ext_info = parse_mft_record(ext_record_bytes, ref_record, record_size)
        if not ext_info:
            continue
        
        # Add data runs from extension record
        for run in ext_info.data_runs:
            all_runs.append(run)
        
        # Recursively check for nested attribute lists
        if ext_info.attribute_list:
            nested_runs = resolve_attribute_list(volume, ext_info, record_size)
            all_runs.extend(nested_runs)
    
    return all_runs


def parse_mft_record(record_bytes: bytes, record_number: int, record_size: int = 1024) -> Optional[NTFSFileInfo]:
    """
    Parses a single 1024-byte MFT record into NTFSFileInfo.
    """
    if len(record_bytes) < record_size:
        return None

    if record_bytes[0:4] != MFT_MAGIC_FILE:
        return None

    data = bytearray(record_bytes[:record_size])
    success, mismatch_count = apply_fixup_array(data, record_size)
    if not success:
        return None

    file_info = NTFSFileInfo(record_number)
    file_info.sequence_number = struct.unpack("<H", data[0x10:0x12])[0]
    flags = struct.unpack("<H", data[0x16:0x18])[0]
    file_info.is_in_use = bool(flags & 0x0001)
    file_info.is_directory = bool(flags & 0x0002)

    first_attr_offset = struct.unpack("<H", data[0x14:0x16])[0]
    used_size = struct.unpack("<I", data[0x18:0x1C])[0]

    curr_offset = first_attr_offset
    best_name = ""
    best_ns = -1

    while curr_offset + 8 <= used_size and curr_offset + 8 <= record_size:
        attr_type, attr_len = struct.unpack("<II", data[curr_offset : curr_offset + 8])
        if attr_type == ATTR_END or attr_len == 0 or curr_offset + attr_len > record_size:
            break

        attr_data = data[curr_offset : curr_offset + attr_len]
        non_resident = attr_data[0x08] != 0
        name_len = attr_data[0x09]
        name_offset = struct.unpack("<H", attr_data[0x0A:0x0C])[0]
        attr_name = ""
        if name_len > 0 and name_offset + (name_len * 2) <= attr_len:
            try:
                attr_name = attr_data[name_offset : name_offset + (name_len * 2)].decode("utf-16le")
            except Exception:
                attr_name = ""

        if attr_type == ATTR_FILE_NAME and not non_resident:
            # Resident $FILE_NAME
            val_len = struct.unpack("<I", attr_data[0x10:0x14])[0]
            val_offset = struct.unpack("<H", attr_data[0x14:0x16])[0]
            if val_offset + val_len <= attr_len and val_len >= 66:
                fn_payload = attr_data[val_offset : val_offset + val_len]
                # Parent directory record (48-bit record num)
                parent_ref = struct.unpack("<Q", fn_payload[0:8])[0]
                parent_rec = parent_ref & 0x0000FFFFFFFFFFFF
                fn_name_len = fn_payload[0x40]
                fn_ns = fn_payload[0x41]
                if 0x42 + (fn_name_len * 2) <= val_len:
                    try:
                        filename = fn_payload[0x42 : 0x42 + (fn_name_len * 2)].decode("utf-16le")
                    except Exception:
                        filename = ""

                    # Choose Win32 / POSIX namespace over DOS (8.3 short name)
                    if filename and (fn_ns in (NS_WIN32, NS_WIN32_DOS, NS_POSIX) or not best_name):
                        best_name = filename
                        best_ns = fn_ns
                        file_info.parent_record_number = parent_rec

        elif attr_type == ATTR_DATA and attr_name == "":
            # Main unnamed $DATA stream
            if not non_resident:
                # Resident Data
                val_len = struct.unpack("<I", attr_data[0x10:0x14])[0]
                val_offset = struct.unpack("<H", attr_data[0x14:0x16])[0]
                if val_offset + val_len <= attr_len:
                    file_info.is_resident = True
                    file_info.resident_data = bytes(attr_data[val_offset : val_offset + val_len])
                    file_info.file_size = val_len
                    file_info.allocated_size = val_len
            else:
                # Non-resident Data
                file_info.is_resident = False
                file_info.allocated_size = struct.unpack("<Q", attr_data[0x28:0x30])[0]
                file_info.file_size = struct.unpack("<Q", attr_data[0x30:0x38])[0]
                data_run_offset = struct.unpack("<H", attr_data[0x20:0x22])[0]
                if data_run_offset < attr_len:
                    runs_bytes = bytes(attr_data[data_run_offset:])
                    file_info.data_runs = decode_data_runs(runs_bytes)

        elif attr_type == ATTR_ATTRIBUTE_LIST:
            # $ATTRIBUTE_LIST - references to additional MFT records for heavily fragmented files
            if not non_resident:
                val_len = struct.unpack("<I", attr_data[0x10:0x14])[0]
                val_offset = struct.unpack("<H", attr_data[0x14:0x16])[0]
                if val_offset + val_len <= attr_len:
                    attr_list_data = bytes(attr_data[val_offset : val_offset + val_len])
                    file_info.attribute_list = attr_list_data

        curr_offset += attr_len

    file_info.name = best_name
    return file_info


def read_all_mft_records(volume: NTFSVolume, max_records: int = 500000, progress_callback=None) -> Dict[int, NTFSFileInfo]:
    """
    Reads and parses all MFT records from the NTFS volume.
    First reads Record 0 ($MFT itself) to find the cluster runs for the entire MFT,
    then parses all records.
    """
    record_size = volume.mft_record_size
    sectors_per_record = record_size // volume.bytes_per_sector
    if sectors_per_record == 0:
        sectors_per_record = 1

    # Read Record 0 ($MFT)
    mft_sector0 = volume.lcn_to_sector(volume.mft_start_lcn)
    record0_bytes = volume.reader.read_sectors(mft_sector0, sectors_per_record)
    if not record0_bytes:
        raise IOError(f"Failed to read $MFT Record 0 at LBA {mft_sector0}")

    record0 = parse_mft_record(record0_bytes, 0, record_size)
    if not record0:
        raise ValueError("Invalid $MFT Record 0")

    # If $MFT data runs are found, we can stream through the entire MFT table
    files: Dict[int, NTFSFileInfo] = {}
    files[0] = record0

    records_per_cluster = volume.bytes_per_cluster // record_size
    if records_per_cluster == 0:
        records_per_cluster = 1

    current_rec_idx = 0

    if record0.data_runs:
        # Stream MFT from cluster runs
        for run in record0.data_runs:
            if run.is_sparse or run.lcn is None:
                current_rec_idx += run.cluster_count * records_per_cluster
                continue

            for cluster_i in range(run.cluster_count):
                cluster_lcn = run.lcn + cluster_i
                cluster_data = volume.read_cluster(cluster_lcn)
                if not cluster_data:
                    current_rec_idx += records_per_cluster
                    continue

                for r in range(records_per_cluster):
                    r_offset = r * record_size
                    if r_offset + record_size <= len(cluster_data):
                        rec_slice = cluster_data[r_offset : r_offset + record_size]
                        if rec_slice.startswith(MFT_MAGIC_FILE):
                            f_info = parse_mft_record(rec_slice, current_rec_idx, record_size)
                            if f_info and f_info.name:
                                files[current_rec_idx] = f_info
                    current_rec_idx += 1

                if progress_callback and current_rec_idx % 1000 == 0:
                    progress_callback(current_rec_idx)

                if current_rec_idx >= max_records:
                    break
            if current_rec_idx >= max_records:
                break
    else:
        # Fallback: scan sequential sectors around $MFT start LCN
        total_clusters_to_scan = min(10000, volume.total_sectors // volume.sectors_per_cluster)
        for cluster_i in range(total_clusters_to_scan):
            cluster_lcn = volume.mft_start_lcn + cluster_i
            cluster_data = volume.read_cluster(cluster_lcn)
            if not cluster_data:
                current_rec_idx += records_per_cluster
                continue

            for r in range(records_per_cluster):
                r_offset = r * record_size
                if r_offset + record_size <= len(cluster_data):
                    rec_slice = cluster_data[r_offset : r_offset + record_size]
                    if rec_slice.startswith(MFT_MAGIC_FILE):
                        f_info = parse_mft_record(rec_slice, current_rec_idx, record_size)
                        if f_info and f_info.name:
                            files[current_rec_idx] = f_info
                current_rec_idx += 1
            if progress_callback and current_rec_idx % 1000 == 0:
                progress_callback(current_rec_idx)

    # Reconstruct full directory paths
    build_full_paths(files)

    # Resolve $ATTRIBUTE_LIST for heavily fragmented files
    for rec_num, f_info in files.items():
        if f_info.attribute_list:
            resolved_runs = resolve_attribute_list(volume, f_info, record_size)
            f_info.data_runs = resolved_runs

    return files


def build_full_paths(files: Dict[int, NTFSFileInfo]):
    """
    Walks up parent directory references to build complete hierarchical file paths.
    """
    # MFT Record 5 is traditionally the root directory ("/")
    memo_paths: Dict[int, str] = {5: ""}

    def get_path(rec_num: int, visited: set) -> str:
        if rec_num in memo_paths:
            return memo_paths[rec_num]
        if rec_num not in files or rec_num in visited:
            return ""

        visited.add(rec_num)
        info = files[rec_num]
        parent_num = info.parent_record_number

        if parent_num == rec_num or parent_num == 5:
            path = info.name
        else:
            parent_path = get_path(parent_num, visited)
            path = f"{parent_path}/{info.name}" if parent_path else info.name

        memo_paths[rec_num] = path
        return path

    for rec_num, info in files.items():
        if not info.name:
            continue
        if rec_num == 5:
            info.full_path = "/"
        else:
            visited_set = set()
            parent_dir = get_path(info.parent_record_number, visited_set)
            if parent_dir and parent_dir != "/":
                info.full_path = f"{parent_dir}/{info.name}"
            else:
                info.full_path = info.name
