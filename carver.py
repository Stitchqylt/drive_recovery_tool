"""
carver.py - Signature-Based Raw File Carver

Extracts files directly by signature (JPEG, PNG, PDF, ZIP/DOCX, SQLite, MP4, GIF)
from raw sectors when filesystem metadata ($MFT or partition tables) is destroyed.
"""

import os
import struct
import csv
from typing import List, Dict, Tuple, Optional, Any
from diskio import RawDiskReader

SIGNATURES = [
    {
        "type": "JPEG",
        "ext": "jpg",
        "header": b"\xFF\xD8\xFF",
        "footer": b"\xFF\xD9",
        "max_size": 50 * 1024 * 1024,
    },
    {
        "type": "PNG",
        "ext": "png",
        "header": b"\x89PNG\r\n\x1a\n",
        "footer": b"IEND\xae\x42\x60\x82",
        "max_size": 50 * 1024 * 1024,
    },
    {
        "type": "PDF",
        "ext": "pdf",
        "header": b"%PDF-",
        "footer": b"%%EOF",
        "max_size": 100 * 1024 * 1024,
    },
    {
        "type": "ZIP_DOCX",
        "ext": "zip",
        "header": b"PK\x03\x04",
        "footer": b"PK\x05\x06",
        "max_size": 200 * 1024 * 1024,
    },
    {
        "type": "SQLite",
        "ext": "sqlite",
        "header": b"SQLite format 3\x00",
        "footer": None,
        "max_size": 500 * 1024 * 1024,
    },
    {
        "type": "GIF",
        "ext": "gif",
        "header": b"GIF89a",
        "footer": b"\x3B",
        "max_size": 50 * 1024 * 1024,
    },
]


class CarvedFile:
    """Represents a discovered and carved file."""
    def __init__(self, file_type: str, ext: str, start_lba: int, size: int, status: str, output_path: str):
        self.file_type = file_type
        self.ext = ext
        self.start_lba = start_lba
        self.size = size
        self.status = status
        self.output_path = output_path


class FileCarver:
    """
    Scans a range of physical sectors to locate and extract orphan files.
    """
    def __init__(self, reader: RawDiskReader, dest_dir: str, timeout_ms: int = 1000):
        self.reader = reader
        self.dest_dir = os.path.abspath(os.path.join(dest_dir, "carved_files"))
        self.timeout_ms = timeout_ms
        self.carved_manifest_path = os.path.join(self.dest_dir, "carved_manifest.csv")
        self.is_cancelled = False
        os.makedirs(self.dest_dir, exist_ok=True)

        if not os.path.exists(self.carved_manifest_path):
            with open(self.carved_manifest_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Type", "StartLBA", "SizeBytes", "Status", "OutputPath"])

    def carve_sectors(
        self,
        start_lba: int,
        sector_count: int,
        progress_callback: Optional[Any] = None,
    ) -> List[CarvedFile]:
        """
        Scans sectors for magic headers and carves detected files.
        Checks at all byte offsets within sectors, not just sector boundaries.
        """
        carved_files = []
        carve_idx = 1
        current_lba = start_lba
        end_lba = start_lba + sector_count
        chunk_sectors = 128  # Read in 64KB chunks
        sector_size = self.reader.sector_size
        max_header_len = max(len(sig["header"]) for sig in SIGNATURES)

        while current_lba < end_lba:
            if self.is_cancelled:
                break

            to_read = min(chunk_sectors, end_lba - current_lba)
            chunk_data = self.reader.read_sectors(current_lba, to_read, timeout_ms=self.timeout_ms)

            if not chunk_data:
                # Unreadable chunk -> advance by 1 sector
                current_lba += 1
                continue

            matched_in_chunk = False
            chunk_len = len(chunk_data)
            
            # Check for signatures at ALL byte offsets within the chunk
            byte_offset = 0
            while byte_offset <= chunk_len - max_header_len:
                exact_lba = current_lba + (byte_offset // sector_size)
                sector_offset = byte_offset % sector_size
                
                matched_sig = None
                for sig in SIGNATURES:
                    header = sig["header"]
                    if chunk_data[byte_offset:byte_offset + len(header)] == header:
                        matched_sig = sig
                        break

                if matched_sig:
                    carved = self._extract_carved_stream(exact_lba, sector_offset, matched_sig, carve_idx)
                    if carved:
                        carved_files.append(carved)
                        carve_idx += 1
                        sectors_jump = (carved.size + sector_size - 1) // sector_size
                        current_lba = exact_lba + max(1, sectors_jump)
                        matched_in_chunk = True
                        break
                
                byte_offset += 1

            if not matched_in_chunk:
                current_lba += to_read

            if progress_callback:
                progress_callback(current_lba - start_lba, sector_count, len(carved_files))

        return carved_files

    def _extract_carved_stream(self, start_lba: int, sector_offset: int, sig: Dict[str, Any], index: int) -> Optional[CarvedFile]:
        """Reads stream from start_lba at sector_offset until footer or max_size."""
        sector_size = self.reader.sector_size
        max_bytes = sig["max_size"]
        max_sectors = (max_bytes + sector_size - 1) // sector_size
        footer = sig.get("footer")
        ftype = sig["type"]
        ext = sig["ext"]

        type_dir = os.path.join(self.dest_dir, ftype)
        os.makedirs(type_dir, exist_ok=True)
        out_name = f"carved_{index:05d}_lba_{start_lba}_off_{sector_offset}.{ext}"
        out_path = os.path.join(type_dir, out_name)

        collected = bytearray()
        bad_count = 0
        found_footer = False

        # Read first sector starting from sector_offset
        if sector_offset > 0:
            first_sec = self.reader.read_sectors(start_lba, 1, timeout_ms=self.timeout_ms)
            if first_sec:
                collected.extend(first_sec[sector_offset:])
            else:
                collected.extend(b"\x00" * (sector_size - sector_offset))
                bad_count += 1
            
            if footer and footer in collected:
                footer_pos = collected.rfind(footer) + len(footer)
                collected = collected[:footer_pos]
                found_footer = True
        
        # Read remaining sectors
        for s in range(max_sectors):
            if self.is_cancelled:
                break
            if found_footer:
                break
                
            sec_data = self.reader.read_sectors(start_lba + s + (1 if sector_offset > 0 else 0), 1, timeout_ms=self.timeout_ms)
            if sec_data:
                collected.extend(sec_data)
            else:
                collected.extend(b"\x00" * sector_size)
                bad_count += 1

            if footer and footer in collected:
                # Trim to footer
                footer_pos = collected.rfind(footer) + len(footer)
                collected = collected[:footer_pos]
                found_footer = True
                break

            # Special case for SQLite: calculate size from DB header at offset 16 (page size) & 28 (page count)
            if ftype == "SQLite" and len(collected) >= 100:
                page_size = struct.unpack(">H", collected[16:18])[0]
                if page_size == 1:
                    page_size = 65536
                page_count = struct.unpack(">I", collected[28:32])[0]
                if page_size > 0 and page_count > 0:
                    expected_db_size = page_size * page_count
                    if expected_db_size <= max_bytes and len(collected) >= expected_db_size:
                        collected = collected[:expected_db_size]
                        found_footer = True
                        break

        if len(collected) < 128:
            return None

        # Trim to max_size
        if len(collected) > max_bytes:
            collected = collected[:max_bytes]

        status = "RECOVERED" if bad_count == 0 else "PARTIAL"
        if status == "PARTIAL":
            out_path += ".partial"

        with open(out_path, "wb") as f:
            f.write(collected)

        with open(self.carved_manifest_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([index, ftype, start_lba, len(collected), status, out_path])

        return CarvedFile(ftype, ext, start_lba, len(collected), status, out_path)
