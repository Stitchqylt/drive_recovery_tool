"""
hash_verifier.py - Forensic Integrity Cryptographic Hash Verifier

Computes streaming SHA-256 and MD5 cryptographic checksums for recovered files
to maintain forensic integrity verification and audit logging.
"""

import hashlib
import os
from typing import Tuple, Dict


class StreamHasher:
    """Computes SHA-256 and MD5 hashes concurrently on data streams."""
    def __init__(self):
        self._sha256 = hashlib.sha256()
        self._md5 = hashlib.md5()
        self.bytes_processed = 0

    def update(self, chunk: bytes):
        if chunk:
            self._sha256.update(chunk)
            self._md5.update(chunk)
            self.bytes_processed += len(chunk)

    @property
    def sha256_hex(self) -> str:
        return self._sha256.hexdigest()

    @property
    def md5_hex(self) -> str:
        return self._md5.hexdigest()

    def get_hashes(self) -> Dict[str, str]:
        return {
            "sha256": self.sha256_hex,
            "md5": self.md5_hex,
        }


def compute_file_hashes(filepath: str, chunk_size: int = 65536) -> Dict[str, str]:
    """Computes SHA-256 and MD5 checksums for an existing file on disk."""
    hasher = StreamHasher()
    if not os.path.exists(filepath):
        return {"sha256": "", "md5": ""}

    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)

    return hasher.get_hashes()
