# Non-Freezing Raw Drive File Recovery Tool (NTFS MVP)

A lightweight data recovery utility designed to safely read failing/degrading physical hard drives **without hanging the operating system**, recovering healthy files and salvaging partial files that standard file copy operations fail to copy.

## Cross-Platform Support

| Platform | Physical Drive Access | GUI | Web Studio | CLI |
|----------|----------------------|-----|------------|-----|
| **Windows** |  `\\.\PhysicalDriveX` (Win32 Direct I/O + CancelIoEx) |  Tkinter (`RUN_GUI.bat`) |  `--web` flag |  `--cli` |
| **macOS** |  `/dev/rdiskX` (O_DIRECT + pread + fcntl F_NOCACHE) |  |  `--web` flag |  `--cli` |
| **Linux** |  `/dev/sdX`, `/dev/nvmeX` (O_DIRECT + pread) |  |  `--web` flag |  `--cli` |

**Key difference**: On macOS/Linux, physical drive access requires **sudo/root** privileges. The Web Studio dashboard works cross-platform and is the recommended interface on non-Windows systems.

---

## Why Standard File Copy Freezes & How This Tool Solves It

1. **The Problem with Standard File Copy**:
   - High-level OS file APIs (Windows `CopyFileEx`, macOS `copyfile`, Linux `cp`) use synchronous cached I/O.
   - When encountering a bad sector with CRC/ECC errors, the kernel storage stack and driver retry repeatedly for minutes, freezing the UI and often locking up the whole OS.
   - If a file has even a single bad sector, the copy operation aborts the entire file or whole operation.

2. **How This Tool Works**:
   - **Direct Raw Physical Drive Access**: Bypasses the OS filesystem driver layer entirely.
   - **Direct Unbuffered I/O**: `FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_OVERLAPPED` (Windows) / `O_DIRECT` + `pread` (Unix) with page-aligned buffers.
   - **Strict Per-Sector Timeouts**: If a sector does not respond within the configured timeout (e.g. 1000ms), the I/O is cancelled. The OS never hangs.
   - **Internal NTFS & $MFT Parser**: Directly parses the Volume Boot Record, Master File Table records, Fixup Sequences (USA), and non-resident Data Runs (cluster chains) from the raw disk.
   - **Fault-Tolerant Zero-Filling**: When unreadable sectors are encountered, the tool inserts zeros in place of the bad clusters, preserves all good clusters, and writes the output file with `.partial` tagging.

---

## System Requirements

### Windows
- **OS**: Windows 10 / 11 / Windows Server (64-bit or 32-bit)
- **Permissions**: **Must be run as Administrator** (required for `\\.\PhysicalDriveX` raw access)
- **Hardware**: SATA-to-USB adapter, USB docking station, or direct SATA motherboard port
- **Python**: Python 3.8+ (Pure standard library, **zero pip packages required**)

### macOS
- **OS**: macOS 10.15+ (Catalina or later)
- **Permissions**: **Must run with sudo** for physical drive access (`/dev/rdiskX`)
- **Python**: Python 3.8+ (install via Homebrew: `brew install python`)
- **Optional**: `smartmontools` for S.M.A.R.T. data (`brew install smartmontools`)

### Linux
- **OS**: Any modern distribution (kernel 4.0+)
- **Permissions**: **Must run with sudo/root** for physical drive access (`/dev/sdX`, `/dev/nvmeX`)
- **Python**: Python 3.8+ (usually pre-installed)
- **Optional**: `smartmontools` for S.M.A.R.T. data (`apt install smartmontools` / `dnf install smartmontools`)

---

## Quick Start Guide

### Option 1: Web Studio Dashboard (Recommended for macOS/Linux, Works on Windows Too)
```bash
# macOS/Linux (run from terminal)
chmod +x RUN_WEB_STUDIO.sh
sudo ./RUN_WEB_STUDIO.sh

# Or directly with Python
sudo python3 main.py --web

# Windows (PowerShell as Administrator)
python main.py --web
```
Opens http://127.0.0.1:8080 in your browser with a modern dashboard for drive selection, S.M.A.R.T. health, live sector heatmap, and file recovery stream.

### Option 2: Windows GUI (Windows Only)
1. Right-click `RUN_GUI.bat` and select **"Run as administrator"**
2. Select target failing drive from the **Physical Drive** dropdown
3. Click **Scan Partitions** to auto-detect the NTFS volume
4. Choose a healthy **Output Folder** on a separate disk
5. Adjust **Read Timeout** (default: `1000 ms`)
6. Click **START RECOVERY**

### Option 3: Command Line Interface (All Platforms)
```bash
# Interactive mode (prompts for drive selection)
sudo python3 main.py --cli

# Direct parameters
sudo python3 main.py --cli --drive /dev/rdisk2 --dest ./recovered_files --timeout 1000

# With multi-pass recovery and file carving
sudo python3 main.py --cli --drive 1 --dest D:\RecoveredFiles --multi-pass --carve
```

### Physical Drive Paths by Platform
| Platform | Physical Drive Path Examples |
|----------|------------------------------|
| Windows | `1` (for `\\.\PhysicalDrive1`), `\\.\PhysicalDrive2` |
| macOS | `/dev/rdisk2`, `/dev/rdisk3` (raw disk, not partitions like `rdisk2s1`) |
| Linux | `/dev/sdb`, `/dev/nvme0n1` (whole disk, not partitions like `sdb1`) |

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
- **`recovery_audit_report.html`**: Professional forensic audit certificate (printable PDF via browser).

---

## Building a Standalone Executable

### Windows (single `.exe`)
```cmd
pip install pyinstaller
pyinstaller --onefile --noconsole --name "DriveRecoveryTool" --add-data "raw_io.py;." --add-data "disk_layout.py;." --add-data "ntfs_parser.py;." --add-data "recovery_engine.py;." --add-data "gui.py;." --add-data "cli.py;." main.py
```
Output: `dist\DriveRecoveryTool.exe` (runs on any Windows without Python)

### macOS/Linux (single binary)
```bash
pip install pyinstaller
pyinstaller --onefile --name "drive-recovery-tool" main.py
```
Output: `dist/drive-recovery-tool` (run with `sudo ./drive-recovery-tool --web`)

---

## Testing & Validation

Run automated integration tests (synthetic NTFS disk image validation):
```bash
python -m unittest tests.test_suite -v
```

---

## Advanced Usage

### Priority Extension Filtering
Recover only specific file types first:
```bash
python main.py --cli --drive /dev/rdisk2 --extensions docx,xlsx,pdf,jpg,sql
```

### Reverse Direction Reading
Read drive from end to beginning (useful for drives failing at the start):
```bash
python main.py --cli --drive 1 --reverse
```

### Resume Previous Session
Auto-resume from existing `recovery.map` journal:
```bash
python main.py --cli --drive 1 --resume
```

### Multi-Pass Recovery (Best for Bad Drives)
```bash
python main.py --cli --drive 1 --multi-pass --carve
```
Phases: 1) Fast Sweep -> 2) File Extraction -> 3) Scraping Bad Zones -> 4) Raw File Carving

---

## License

MIT License - See LICENSE file for details.
