# Non-Freezing Windows Raw Drive File Recovery Tool (NTFS MVP)

A lightweight data recovery utility designed to safely read failing/degrading physical hard drives on Windows **without hanging the operating system**, recovering healthy files and salvaging partial files that Windows Explorer fails to copy.

---

## Why Windows Explorer Freezes & How This Tool Solves It

1. **The Problem with Windows Explorer**:
   - High-level Windows file APIs (`CopyFileEx`, Explorer drag-and-drop) use synchronous cached I/O.
   - When encountering a bad sector with CRC/ECC errors, the Windows kernel storage stack and driver retry repeatedly for minutes, freezing Explorer and often locking up the whole OS UI.
   - If a file has even a single bad sector, Explorer aborts the entire file or whole copy operation.

2. **How This Tool Works**:
   - **Direct Raw Physical Drive Access (`\\.\PhysicalDriveX`)**: Bypasses the Windows filesystem driver layer entirely.
   - **Direct Unbuffered Asynchronous I/O (`FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_OVERLAPPED`)**: Directly addresses raw sectors on disk with page-aligned buffers.
   - **Strict Per-Sector Timeouts with `CancelIoEx`**: If a sector does not respond within the configured timeout (e.g. 1000ms), an immediate hardware I/O cancellation is dispatched. Windows never hangs.
   - **Internal NTFS & $MFT Parser**: Directly parses the Volume Boot Record, Master File Table records, Fixup Sequences (USA), and non-resident Data Runs (cluster chains) from the raw disk.
   - **Fault-Tolerant Zero-Filling**: When unreadable sectors are encountered, the tool inserts zeros in place of the bad clusters, preserves all good clusters, and writes the output file with `.partial` tagging.

---

## System Requirements
- **OS**: Windows 10 / 11 / Windows Server (64-bit or 32-bit).
- **Permissions**: **Must be run as Administrator** (required for `\\.\PhysicalDriveX` raw access).
- **Hardware**: SATA-to-USB adapter, USB docking station, or direct SATA motherboard port connected to the test drive.
- **Python**: Python 3.8+ (Pure standard library, **zero pip packages required**).

---

## Quick Start Guide

### Option 1: Running the GUI (Recommended for Field Use)
1. Right-click `RUN_GUI.bat` and select **"Run as administrator"** (or run `python main.py` in an elevated terminal).
2. In the GUI window:
   - Select the target failing drive from the **Physical Drive** dropdown (e.g. `PhysicalDrive1`).
   - Click **Scan Partitions** to auto-detect the NTFS volume.
   - Choose a healthy **Output Folder** on a separate disk to save recovered files.
   - Adjust **Read Timeout** (default: `1000 ms`).
   - Click **START RECOVERY**.
3. Watch real-time metrics: Good Sectors, Bad Sectors, Recovered, Partial, and Live Activity Log.

### Option 2: Running via Command Line (CLI)
1. Open an elevated Command Prompt / PowerShell as Administrator.
2. Run:
   ```cmd
   python main.py --cli
   ```
3. Or specify parameters directly:
   ```cmd
   python main.py --cli --drive 1 --dest D:\RecoveredFiles --timeout 1000
   ```

---

## Output Structure & Reports

When recovery finishes, the destination folder contains:
- **Recovered Files**: Reconstructed in their original folder hierarchy.
  - Fully readable files: saved with original filenames (e.g., `Report.docx`).
  - Partially readable files: saved with `.partial` suffix (e.g., `Archive.zip.partial`) with bad sectors zero-filled.
- **`recovery_manifest.csv`**: Full spreadsheet log of every scanned file:
  - Record Number, Original Path, Status (`RECOVERED` / `PARTIAL` / `FAILED`), File Size, Good Clusters, Bad Clusters, Output Path.
- **`bad_sectors.log`**: Exact list of every unreadable LBA sector with timestamps.
- **`recovery.log`**: Chronological debug and execution trace.

---

## Building a Standalone Single `.exe` (No Python Installation Needed)

If you want to create a single `.exe` file that runs on any Windows machine without Python installed:
1. Install PyInstaller once:
   ```cmd
   pip install pyinstaller
   ```
2. Double-click `BUILD_EXE.bat` or run:
   ```cmd
   pyinstaller --onefile --noconsole --name "DriveRecoveryTool" --add-data "raw_io.py;." --add-data "disk_layout.py;." --add-data "ntfs_parser.py;." --add-data "recovery_engine.py;." --add-data "gui.py;." --add-data "cli.py;." main.py
   ```
3. The standalone executable will be created in `dist\DriveRecoveryTool.exe`.

---

## Testing & Validation

To run automated integration tests (synthetic NTFS disk image validation):
```cmd
python -m unittest tests/test_suite.py
```
