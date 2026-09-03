"""
raw_io.py - Low-Level Physical Drive Reader with Win32 Overlapped Direct I/O and Timeouts

Provides sector-level direct read access to physical drives (\\\\.\\PhysicalDriveX)
on Windows using unbuffered, asynchronous (overlapped) I/O with CancelIoEx timeouts
to prevent Windows OS hangs on damaged or unreadable sectors.
"""

import sys
import os
import time
import struct
import platform
from typing import Optional, Tuple, List, Dict, Any

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    # Win32 Constants
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3

    # Direct & Overlapped I/O flags
    FILE_FLAG_NO_BUFFERING = 0x20000000
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    FILE_FLAG_OVERLAPPED = 0x40000000

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    # Wait constants
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF

    # Memory allocation
    MEM_COMMIT = 0x00001000
    MEM_RESERVE = 0x00002000
    PAGE_READWRITE = 0x04
    MEM_RELEASE = 0x00008000

    # Win32 Error Codes
    ERROR_SUCCESS = 0
    ERROR_IO_PENDING = 997
    ERROR_OPERATION_ABORTED = 995
    ERROR_CRC = 23
    ERROR_SECTOR_NOT_FOUND = 27
    ERROR_NOT_READY = 21
    ERROR_DEVICE_NOT_CONNECTED = 1167
    ERROR_IO_DEVICE = 1117

    # Overlapped structure definition
    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    # Win32 Function Signatures
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    CreateFileW.restype = wintypes.HANDLE

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    CreateEventW = kernel32.CreateEventW
    CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    CreateEventW.restype = wintypes.HANDLE

    ReadFile = kernel32.ReadFile
    ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    ReadFile.restype = wintypes.BOOL

    WaitForSingleObject = kernel32.WaitForSingleObject
    WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    WaitForSingleObject.restype = wintypes.DWORD

    CancelIoEx = kernel32.CancelIoEx
    CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    CancelIoEx.restype = wintypes.BOOL

    GetOverlappedResult = kernel32.GetOverlappedResult
    GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(OVERLAPPED),
        wintypes.LPDWORD,
        wintypes.BOOL,
    ]
    GetOverlappedResult.restype = wintypes.BOOL

    VirtualAlloc = kernel32.VirtualAlloc
    VirtualAlloc.argtypes = [
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    VirtualAlloc.restype = wintypes.LPVOID

    VirtualFree = kernel32.VirtualFree
    VirtualFree.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]
    VirtualFree.restype = wintypes.BOOL

    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    DeviceIoControl.restype = wintypes.BOOL

    IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
    IOCTL_DISK_GET_LENGTH_INFO = 0x0007405F


class SectorBuffer:
    """
    Allocates page-aligned memory buffer for unbuffered direct disk I/O.
    """
    def __init__(self, size: int):
        self.size = size
        if IS_WINDOWS:
            self.ptr = VirtualAlloc(None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
            if not self.ptr:
                raise MemoryError(f"VirtualAlloc failed for {size} bytes")
        else:
            self._buf = bytearray(size)
            self.ptr = None

    def get_bytes(self, length: int) -> bytes:
        if IS_WINDOWS:
            return ctypes.string_at(self.ptr, min(length, self.size))
        else:
            return bytes(self._buf[:length])

    def close(self):
        if IS_WINDOWS and self.ptr:
            VirtualFree(self.ptr, 0, MEM_RELEASE)
            self.ptr = None


class RawDiskReader:
    """
    Reads physical sectors from \\\\.\\PhysicalDriveX or raw image files with strict timeout controls.
    """
    def __init__(self, device_path: str, sector_size: int = 512, default_timeout_ms: int = 1500):
        self.device_path = device_path
        self.sector_size = sector_size
        self.default_timeout_ms = default_timeout_ms
        self.handle = None
        self.is_image_file = not device_path.startswith(r"\\.\\") and not device_path.startswith(r"\\.\PhysicalDrive")
        self._file_obj = None
        self._buffer = None
        self._event = None
        self.disk_size_bytes = 0

        self.open()

    def open(self):
        """Opens the physical drive or image file."""
        if IS_WINDOWS and not self.is_image_file:
            flags = FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH | FILE_FLAG_OVERLAPPED
            self.handle = CreateFileW(
                self.device_path,
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                flags,
                None,
            )
            if self.handle == INVALID_HANDLE_VALUE or self.handle == 0:
                err = ctypes.get_last_error()
                raise PermissionError(
                    f"Failed to open {self.device_path} (Win32 Error {err}). "
                    f"Ensure this program is run as Administrator."
                )

            self._event = CreateEventW(None, True, False, None)
            if not self._event:
                self.close()
                raise RuntimeError("Failed to create Win32 Event for overlapped I/O")

            # Allocate default sector buffer (e.g. 1MB chunk buffer)
            self._buffer = SectorBuffer(1024 * 1024)
            self._query_disk_size()
        else:
            # File or non-Windows system fallback
            try:
                self._file_obj = open(self.device_path, "rb")
                self._file_obj.seek(0, os.SEEK_END)
                self.disk_size_bytes = self._file_obj.tell()
                self._file_obj.seek(0)
            except Exception as e:
                raise IOError(f"Failed to open disk image {self.device_path}: {e}")

    def _query_disk_size(self):
        """Attempts to query disk size via DeviceIoControl."""
        if not IS_WINDOWS or self.is_image_file or not self.handle:
            return
        
        # Try IOCTL_DISK_GET_LENGTH_INFO (8 bytes output = int64 length)
        length_buf = ctypes.c_int64(0)
        bytes_returned = wintypes.DWORD(0)
        success = DeviceIoControl(
            self.handle,
            IOCTL_DISK_GET_LENGTH_INFO,
            None,
            0,
            ctypes.byref(length_buf),
            ctypes.sizeof(length_buf),
            ctypes.byref(bytes_returned),
            None,
        )
        if success:
            self.disk_size_bytes = length_buf.value

    def read_sectors(self, start_sector: int, num_sectors: int, timeout_ms: Optional[int] = None) -> Optional[bytes]:
        """
        Reads a contiguous block of sectors starting at start_sector (LBA).
        Returns bytes on success, or None if read fails / times out.
        """
        byte_offset = start_sector * self.sector_size
        byte_length = num_sectors * self.sector_size
        timeout = timeout_ms if timeout_ms is not None else self.default_timeout_ms

        if not IS_WINDOWS or self.is_image_file:
            return self._read_fallback(byte_offset, byte_length)

        return self._read_win32_overlapped(byte_offset, byte_length, timeout)

    def _read_win32_overlapped(self, byte_offset: int, byte_length: int, timeout_ms: int) -> Optional[bytes]:
        """
        Executes unbuffered asynchronous read with explicit timeout and CancelIoEx.
        """
        # Ensure buffer size is sufficient
        if not self._buffer or self._buffer.size < byte_length:
            if self._buffer:
                self._buffer.close()
            # Allocate aligned buffer with extra headroom
            alloc_size = ((byte_length + 4095) // 4096) * 4096
            self._buffer = SectorBuffer(alloc_size)

        overlapped = OVERLAPPED()
        overlapped.Offset = byte_offset & 0xFFFFFFFF
        overlapped.OffsetHigh = (byte_offset >> 32) & 0xFFFFFFFF
        overlapped.hEvent = self._event

        bytes_read = wintypes.DWORD(0)
        read_success = ReadFile(
            self.handle,
            self._buffer.ptr,
            byte_length,
            ctypes.byref(bytes_read),
            ctypes.byref(overlapped),
        )

        if not read_success:
            err = ctypes.get_last_error()
            if err == ERROR_IO_PENDING:
                # Asynchronous I/O pending, wait with timeout
                wait_res = WaitForSingleObject(self._event, timeout_ms)
                if wait_res == WAIT_OBJECT_0:
                    # Completed within timeout
                    got_result = GetOverlappedResult(
                        self.handle,
                        ctypes.byref(overlapped),
                        ctypes.byref(bytes_read),
                        False,
                    )
                    if got_result and bytes_read.value == byte_length:
                        return self._buffer.get_bytes(byte_length)
                    else:
                        return None
                elif wait_res == WAIT_TIMEOUT:
                    # Timeout exceeded! Cancel I/O to avoid hanging Windows
                    CancelIoEx(self.handle, ctypes.byref(overlapped))
                    # Wait briefly for cancellation to finalize
                    WaitForSingleObject(self._event, 200)
                    return None
                else:
                    # Wait failed
                    CancelIoEx(self.handle, ctypes.byref(overlapped))
                    return None
            else:
                # Direct hardware error (e.g. CRC error, bad sector)
                return None
        else:
            # Immediate synchronous completion
            return self._buffer.get_bytes(byte_length)

    def _read_fallback(self, byte_offset: int, byte_length: int) -> Optional[bytes]:
        """Fallback for disk images or non-Windows environments."""
        try:
            self._file_obj.seek(byte_offset)
            data = self._file_obj.read(byte_length)
            if len(data) == byte_length:
                return data
            return None
        except Exception:
            return None

    def read_cluster(self, lba_offset: int, cluster_index: int, sectors_per_cluster: int, timeout_ms: Optional[int] = None) -> Optional[bytes]:
        """
        Reads a single cluster at given partition LBA offset and cluster index.
        """
        start_sector = lba_offset + (cluster_index * sectors_per_cluster)
        return self.read_sectors(start_sector, sectors_per_cluster, timeout_ms)

    def close(self):
        """Releases handles and allocated resources."""
        if IS_WINDOWS:
            if self._buffer:
                self._buffer.close()
                self._buffer = None
            if self._event:
                CloseHandle(self._event)
                self._event = None
            if self.handle and self.handle != INVALID_HANDLE_VALUE:
                CloseHandle(self.handle)
                self.handle = None
        if self._file_obj:
            self._file_obj.close()
            self._file_obj = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def list_physical_drives() -> List[Dict[str, Any]]:
    """
    Lists physical drives available on the Windows system.
    Returns list of dicts with drive index, device path, and size.
    """
    drives = []
    if not IS_WINDOWS:
        # Return mock / local testing drive representation
        drives.append({
            "index": 0,
            "device_path": "mock_drive.img",
            "name": "Local Image / Mock Drive (Non-Windows)",
            "size_bytes": 1024 * 1024 * 100,
            "size_str": "100 MB",
        })
        return drives

    # Scan PhysicalDrive0 up to PhysicalDrive32
    for drive_idx in range(32):
        device_path = rf"\\.\PhysicalDrive{drive_idx}"
        try:
            reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=500)
            size_gb = reader.disk_size_bytes / (1024**3) if reader.disk_size_bytes > 0 else 0
            size_str = f"{size_gb:.2f} GB" if size_gb > 0 else "Unknown Size"
            drives.append({
                "index": drive_idx,
                "device_path": device_path,
                "name": f"PhysicalDrive{drive_idx} ({size_str})",
                "size_bytes": reader.disk_size_bytes,
                "size_str": size_str,
            })
            reader.close()
        except PermissionError:
            # Drive exists but access denied (or needs admin)
            drives.append({
                "index": drive_idx,
                "device_path": device_path,
                "name": f"PhysicalDrive{drive_idx} (Access Denied / Admin Required)",
                "size_bytes": 0,
                "size_str": "Unknown",
            })
        except Exception:
            # Drive does not exist or cannot be opened
            continue

    return drives


def is_admin() -> bool:
    """Checks if the current process has Windows Administrator privileges."""
    if not IS_WINDOWS:
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False
