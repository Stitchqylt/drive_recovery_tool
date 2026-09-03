"""
gui.py - Rich Visual Desktop Recovery Interface (Sector Grid + Live File Tree)

Features:
1. Drive Selection dropdown & NTFS partition scanner.
2. 1-Click Start / Pause / Resume button.
3. Live Overall Progress Bar.
4. Colored Sector Status Heatmap Grid (Canvas):
   - Green: Readable / Healthy
   - Red: Bad / Unreadable (Zero-filled)
   - Yellow: Slow / Skipped
   - Dark Gray: Untouched
5. Live Hierarchical File Tree (ttk.Treeview) with color-coded status tags:
   - 🟢 RECOVERED
   - 🟡 PARTIAL (Zero-filled)
   - 🔴 FAILED
6. Copy CLI Command Exporter.
"""

import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Dict, Optional, Any

from raw_io import RawDiskReader, list_physical_drives, is_admin
from disk_layout import scan_partitions, PartitionInfo
from ntfs_parser import NTFSVolume, read_all_mft_records, NTFSFileInfo
from recovery_engine import RecoveryEngine, RecoveryStats
from mapfile import (
    RecoveryMapFile,
    STATE_UNTOUCHED,
    STATE_RECOVERED,
    STATE_SKIPPED,
    STATE_BAD,
    STATE_SCRAPED,
)
from multipass_scheduler import MultiPassScheduler

# Color Palette for Sector Heatmap
COLOR_UNTOUCHED = "#374151"   # Dark Gray
COLOR_RECOVERED = "#10b981"   # Emerald Green
COLOR_SKIPPED = "#f59e0b"     # Amber / Yellow
COLOR_BAD = "#ef4444"         # Bright Red
COLOR_SCRAPED = "#06b6d4"     # Cyan / Scraped


class SectorGridCanvas(tk.Canvas):
    """
    Renders a live 2D colored sector/cluster status grid.
    Visualizes thousands of disk blocks smoothly.
    """
    def __init__(self, parent, rows: int = 12, cols: int = 60, cell_size: int = 11, **kwargs):
        super().__init__(parent, bg="#1e1e24", highlightthickness=0, **kwargs)
        self.rows = rows
        self.cols = cols
        self.total_cells = rows * cols
        self.cell_size = cell_size
        self.cell_gap = 2
        self.cells = []
        self._init_grid()

    def _init_grid(self):
        self.delete("all")
        self.cells.clear()
        for r in range(self.rows):
            for c in range(self.cols):
                x0 = c * (self.cell_size + self.cell_gap) + 6
                y0 = r * (self.cell_size + self.cell_gap) + 6
                x1 = x0 + self.cell_size
                y1 = y0 + self.cell_size
                rect_id = self.create_rectangle(
                    x0, y0, x1, y1,
                    fill=COLOR_UNTOUCHED,
                    outline="#2a2e39",
                    width=1,
                )
                self.cells.append(rect_id)

    def set_cell_state(self, cell_index: int, state: int):
        if 0 <= cell_index < len(self.cells):
            color = COLOR_UNTOUCHED
            if state == STATE_RECOVERED:
                color = COLOR_RECOVERED
            elif state == STATE_SKIPPED:
                color = COLOR_SKIPPED
            elif state == STATE_BAD:
                color = COLOR_BAD
            elif state == STATE_SCRAPED:
                color = COLOR_SCRAPED
            self.itemconfig(self.cells[cell_index], fill=color)

    def update_from_mapfile(self, mapfile: RecoveryMapFile):
        """Maps mapfile interval ranges onto the grid cells."""
        if not mapfile or mapfile.total_sectors == 0:
            return

        total_sec = mapfile.total_sectors
        sec_per_cell = total_sec / self.total_cells

        for cell_idx in range(self.total_cells):
            cell_start = int(cell_idx * sec_per_cell)
            cell_end = int((cell_idx + 1) * sec_per_cell)

            dominant_state = STATE_UNTOUCHED
            for iv in mapfile.intervals:
                if iv.end_lba > cell_start and iv.start_lba < cell_end:
                    if iv.state == STATE_BAD:
                        dominant_state = STATE_BAD
                        break
                    elif iv.state == STATE_SKIPPED and dominant_state != STATE_BAD:
                        dominant_state = STATE_SKIPPED
                    elif iv.state == STATE_RECOVERED and dominant_state == STATE_UNTOUCHED:
                        dominant_state = STATE_RECOVERED
                    elif iv.state == STATE_SCRAPED and dominant_state in (STATE_UNTOUCHED, STATE_RECOVERED):
                        dominant_state = STATE_SCRAPED

            self.set_cell_state(cell_idx, dominant_state)


class RecoveryApp(tk.Tk):
    """Main Recovery GUI Window."""
    def __init__(
        self,
        initial_drive: Optional[str] = None,
        initial_dest: Optional[str] = None,
        initial_timeout: int = 1000,
        initial_include_sys: bool = False,
        initial_multi_pass: bool = False,
        initial_carve: bool = False,
    ):
        super().__init__()
        self.title("NTFS Raw Drive Recovery Tool (Visual Grid & File Tree)")
        self.geometry("980x820")
        self.minsize(840, 680)

        self.initial_drive = initial_drive
        self.initial_dest = initial_dest
        self.initial_timeout = initial_timeout
        self.initial_include_sys = initial_include_sys
        self.initial_multi_pass = initial_multi_pass
        self.initial_carve = initial_carve

        self.reader: Optional[RawDiskReader] = None
        self.volume: Optional[NTFSVolume] = None
        self.partitions: List[PartitionInfo] = []
        self.files: Dict[int, NTFSFileInfo] = {}
        self.engine: Optional[RecoveryEngine] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.is_running = False

        self._setup_ui()
        self._check_admin_privileges()
        self.refresh_drives()

        if self.initial_dest:
            self.dest_var.set(os.path.abspath(self.initial_dest))
            self.check_existing_mapfile()

    def _check_admin_privileges(self):
        if not is_admin():
            messagebox.showwarning(
                "Administrator Rights Required",
                "This tool requires Administrator privileges to directly access physical drives (\\\\.\\PhysicalDriveX).\n\n"
                "Please restart the application by right-clicking and selecting 'Run as Administrator'.",
            )

    def _setup_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # Custom styling
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=22)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ==========================================
        # Top Panel: Source Drive & Destination Row
        # ==========================================
        top_frame = ttk.LabelFrame(main_frame, text=" 1. Drive Selection & Destination ", padding="8")
        top_frame.pack(fill=tk.X, pady=(0, 6))

        # Row 0: Drive & Partition
        ttk.Label(top_frame, text="Source Drive:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=3)
        self.drive_combo = ttk.Combobox(top_frame, state="readonly", width=42)
        self.drive_combo.grid(row=0, column=1, sticky=tk.W, padx=4, pady=3)
        self.drive_combo.bind("<<ComboboxSelected>>", self.on_drive_selected)

        btn_refresh = ttk.Button(top_frame, text="Refresh", width=8, command=self.refresh_drives)
        btn_refresh.grid(row=0, column=2, padx=3, pady=3)

        btn_file = ttk.Button(top_frame, text="Disk Image...", command=self.browse_disk_image)
        btn_file.grid(row=0, column=3, padx=3, pady=3)

        ttk.Label(top_frame, text="NTFS Partition:").grid(row=0, column=4, sticky=tk.W, padx=(12, 4), pady=3)
        self.part_combo = ttk.Combobox(top_frame, state="readonly", width=28)
        self.part_combo.grid(row=0, column=5, sticky=tk.W, padx=4, pady=3)

        # Row 1: Output Destination & Options
        ttk.Label(top_frame, text="Output Folder:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=3)
        default_dest = os.path.abspath(self.initial_dest or "recovered_files")
        self.dest_var = tk.StringVar(value=default_dest)
        self.dest_entry = ttk.Entry(top_frame, textvariable=self.dest_var, width=44)
        self.dest_entry.grid(row=1, column=1, sticky=tk.W, padx=4, pady=3)
        self.dest_var.trace_add("write", lambda *args: self.check_existing_mapfile())

        btn_browse_dest = ttk.Button(top_frame, text="Browse...", width=8, command=self.browse_dest_dir)
        btn_browse_dest.grid(row=1, column=2, padx=3, pady=3)

        ttk.Label(top_frame, text="Timeout (ms):").grid(row=1, column=4, sticky=tk.W, padx=(12, 4), pady=3)
        self.timeout_var = tk.IntVar(value=self.initial_timeout)
        self.timeout_spin = ttk.Spinbox(top_frame, from_=200, to=10000, increment=200, textvariable=self.timeout_var, width=8)
        self.timeout_spin.grid(row=1, column=5, sticky=tk.W, padx=4, pady=3)

        # Row 2: Checkboxes
        opt_frame = ttk.Frame(top_frame)
        opt_frame.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))

        self.multi_pass_var = tk.BooleanVar(value=self.initial_multi_pass)
        ttk.Checkbutton(opt_frame, text="Multi-Pass Adaptive Sweep", variable=self.multi_pass_var).pack(side=tk.LEFT, padx=(4, 10))

        self.carve_var = tk.BooleanVar(value=self.initial_carve)
        ttk.Checkbutton(opt_frame, text="Raw File Carver (Orphans)", variable=self.carve_var).pack(side=tk.LEFT, padx=10)

        self.include_sys_var = tk.BooleanVar(value=self.initial_include_sys)
        ttk.Checkbutton(opt_frame, text="Include System Files ($MFT)", variable=self.include_sys_var).pack(side=tk.LEFT, padx=10)

        self.lbl_resume_info = ttk.Label(opt_frame, text="", foreground="#1565c0", font=("Segoe UI", 9, "bold"))
        self.lbl_resume_info.pack(side=tk.LEFT, padx=15)

        # ==========================================
        # Control Buttons & Progress Bar
        # ==========================================
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill=tk.X, pady=(0, 6))

        self.btn_start = tk.Button(
            ctrl_frame,
            text="START RECOVERY",
            command=self.start_recovery,
            bg="#2e7d32",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=6,
            relief=tk.RAISED,
        )
        self.btn_start.pack(side=tk.LEFT, padx=(2, 6))

        self.btn_cancel = tk.Button(
            ctrl_frame,
            text="Cancel / Pause",
            command=self.cancel_recovery,
            state=tk.DISABLED,
            bg="#c62828",
            fg="white",
            font=("Segoe UI", 10),
            padx=12,
            pady=6,
        )
        self.btn_cancel.pack(side=tk.LEFT, padx=6)

        btn_copy_cli = ttk.Button(ctrl_frame, text="Copy CLI Command", command=self.copy_cli_command)
        btn_copy_cli.pack(side=tk.LEFT, padx=6)

        # Progress bar
        self.prog_bar = ttk.Progressbar(ctrl_frame, orient=tk.HORIZONTAL, mode="determinate")
        self.prog_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)

        self.lbl_pct = ttk.Label(ctrl_frame, text="0.0%", font=("Segoe UI", 10, "bold"))
        self.lbl_pct.pack(side=tk.RIGHT, padx=4)

        # ==========================================
        # Middle Section: Sector Status Grid (Canvas)
        # ==========================================
        grid_container = ttk.LabelFrame(main_frame, text=" 2. Sector Status Heatmap Grid ", padding="6")
        grid_container.pack(fill=tk.X, pady=(0, 6))

        # Legend Bar
        legend_frame = ttk.Frame(grid_container)
        legend_frame.pack(fill=tk.X, pady=(0, 4))

        self._create_legend_item(legend_frame, COLOR_RECOVERED, "🟢 Readable / Good")
        self._create_legend_item(legend_frame, COLOR_BAD, "🔴 Bad / Unreadable (Zero-Filled)")
        self._create_legend_item(legend_frame, COLOR_SKIPPED, "🟡 Slow / Skipped")
        self._create_legend_item(legend_frame, COLOR_SCRAPED, "🔵 Scraped")
        self._create_legend_item(legend_frame, COLOR_UNTOUCHED, "⚪ Untouched")

        # Sector Canvas
        self.sector_grid = SectorGridCanvas(grid_container, rows=8, cols=72, cell_size=10, height=105)
        self.sector_grid.pack(fill=tk.X, expand=True, pady=2)

        # Status counts row
        metric_bar = ttk.Frame(grid_container)
        metric_bar.pack(fill=tk.X, pady=(4, 0))

        self.lbl_good_sec = ttk.Label(metric_bar, text="Good Sectors: 0", foreground="#10b981", font=("Segoe UI", 9, "bold"))
        self.lbl_good_sec.pack(side=tk.LEFT, padx=(4, 20))

        self.lbl_bad_sec = ttk.Label(metric_bar, text="Bad Sectors: 0", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        self.lbl_bad_sec.pack(side=tk.LEFT, padx=20)

        self.lbl_recovered_cnt = ttk.Label(metric_bar, text="Files Recovered: 0", foreground="#10b981")
        self.lbl_recovered_cnt.pack(side=tk.LEFT, padx=20)

        self.lbl_partial_cnt = ttk.Label(metric_bar, text="Partial Files: 0", foreground="#f59e0b")
        self.lbl_partial_cnt.pack(side=tk.LEFT, padx=20)

        self.lbl_failed_cnt = ttk.Label(metric_bar, text="Failed: 0", foreground="#ef4444")
        self.lbl_failed_cnt.pack(side=tk.LEFT, padx=20)

        # ==========================================
        # Bottom Section: Paned Window (File Tree & Activity Log)
        # ==========================================
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Top Pane: Live File Tree
        tree_frame = ttk.LabelFrame(paned, text=" 3. Recovered Files & Status Tree ", padding="4")
        paned.add(tree_frame, weight=3)

        tree_cols = ("status", "size", "good_c", "bad_c")
        self.file_tree = ttk.Treeview(tree_frame, columns=tree_cols, selectmode="browse", height=8)
        self.file_tree.heading("#0", text="File / Folder Path", anchor=tk.W)
        self.file_tree.heading("status", text="Recovery Status", anchor=tk.CENTER)
        self.file_tree.heading("size", text="File Size", anchor=tk.E)
        self.file_tree.heading("good_c", text="Good Clusters", anchor=tk.E)
        self.file_tree.heading("bad_c", text="Bad Clusters", anchor=tk.E)

        self.file_tree.column("#0", width=420, stretch=True)
        self.file_tree.column("status", width=140, anchor=tk.CENTER)
        self.file_tree.column("size", width=100, anchor=tk.E)
        self.file_tree.column("good_c", width=100, anchor=tk.E)
        self.file_tree.column("bad_c", width=100, anchor=tk.E)

        # Style tags
        self.file_tree.tag_configure("RECOVERED", foreground="#059669")
        self.file_tree.tag_configure("PARTIAL", foreground="#d97706")
        self.file_tree.tag_configure("FAILED", foreground="#dc2626")
        self.file_tree.tag_configure("PENDING", foreground="#6b7280")

        tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=tree_scroll_y.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # Bottom Pane: Live Activity Log
        log_frame = ttk.LabelFrame(paned, text=" Live Activity Log ", padding="4")
        paned.add(log_frame, weight=2)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=5, font=("Consolas", 9), bg="#18181b", fg="#d4d4d8")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scroll.set)

    def _create_legend_item(self, parent, color: str, text: str):
        item = ttk.Frame(parent)
        item.pack(side=tk.LEFT, padx=8)
        lbl_box = tk.Label(item, bg=color, width=2, height=1, relief=tk.SOLID, borderwidth=1)
        lbl_box.pack(side=tk.LEFT, padx=(0, 4))
        lbl_txt = ttk.Label(item, text=text, font=("Segoe UI", 8))
        lbl_txt.pack(side=tk.LEFT)

    def log_message(self, message: str, level: str = "INFO"):
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] [{level}] {message}\n")
        self.log_text.see(tk.END)

    def check_existing_mapfile(self):
        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            return
        map_path = os.path.join(dest_dir, "recovery.map")
        if os.path.exists(map_path) and os.path.getsize(map_path) > 0:
            try:
                mf = RecoveryMapFile(map_path)
                st = mf.get_stats()
                self.lbl_resume_info.config(
                    text=f"[i] Previous Session: {st['recovered']:,} good, {st['bad']:,} bad sectors."
                )
                self.sector_grid.update_from_mapfile(mf)
            except Exception:
                self.lbl_resume_info.config(text="[i] Existing recovery.map found.")
        else:
            self.lbl_resume_info.config(text="")

    def copy_cli_command(self):
        sel_idx = self.drive_combo.current()
        drive_arg = ""
        if 0 <= sel_idx < len(self.drive_list):
            d = self.drive_list[sel_idx]
            drive_arg = f"--drive {d['index']}" if d["index"] >= 0 else f'--drive "{d["device_path"]}"'

        part_idx = self.part_combo.current() + 1
        dest = self.dest_var.get().strip()
        timeout = self.timeout_var.get()

        cmd = ["python main.py --cli"]
        if drive_arg:
            cmd.append(drive_arg)
        if part_idx > 0:
            cmd.append(f"--partition {part_idx}")
        if dest:
            cmd.append(f'--dest "{dest}"')
        if timeout != 1000:
            cmd.append(f"--timeout {timeout}")
        if self.multi_pass_var.get():
            cmd.append("--multi-pass")
        if self.carve_var.get():
            cmd.append("--carve")
        if self.include_sys_var.get():
            cmd.append("--include-system")

        full_cmd = " ".join(cmd)
        self.clipboard_clear()
        self.clipboard_append(full_cmd)
        messagebox.showinfo(
            "CLI Command Copied",
            f"The matching CLI command has been copied to your clipboard:\n\n{full_cmd}\n\n"
            "You can run this directly in Command Prompt or PowerShell.",
        )

    def refresh_drives(self):
        self.drive_combo["values"] = []
        drives = list_physical_drives()
        self.drive_list = drives
        combo_values = []
        target_idx = 0
        for i, d in enumerate(drives):
            combo_values.append(f"{d['name']} ({d['device_path']})")
            if self.initial_drive and (str(d["index"]) == self.initial_drive or d["device_path"] == self.initial_drive):
                target_idx = i

        self.drive_combo["values"] = combo_values
        if combo_values:
            self.drive_combo.current(target_idx)
            self.scan_selected_drive_partitions()
        else:
            self.log_message("No physical drives detected.", "WARN")

    def on_drive_selected(self, event=None):
        self.scan_selected_drive_partitions()

    def browse_disk_image(self):
        file_path = filedialog.askopenfilename(
            title="Select Raw Disk Image File",
            filetypes=[("Disk Images (*.img;*.raw;*.bin;*.vhd)", "*.img;*.raw;*.bin;*.vhd"), ("All Files", "*.*")],
        )
        if file_path:
            self.drive_list = [{"index": -1, "device_path": file_path, "name": f"Image: {os.path.basename(file_path)}"}]
            self.drive_combo["values"] = [f"Image: {os.path.basename(file_path)} ({file_path})"]
            self.drive_combo.current(0)
            self.scan_selected_drive_partitions()

    def browse_dest_dir(self):
        dir_path = filedialog.askdirectory(title="Select Destination Recovery Directory")
        if dir_path:
            self.dest_var.set(dir_path)
            self.check_existing_mapfile()

    def scan_selected_drive_partitions(self):
        sel_idx = self.drive_combo.current()
        if sel_idx < 0 or sel_idx >= len(self.drive_list):
            return

        device_path = self.drive_list[sel_idx]["device_path"]
        self.log_message(f"Scanning partitions on {device_path}...")

        try:
            if self.reader:
                self.reader.close()
            self.reader = RawDiskReader(device_path, sector_size=512, default_timeout_ms=self.timeout_var.get())
            self.partitions = scan_partitions(self.reader)

            part_values = []
            ntfs_idx = -1
            for i, p in enumerate(self.partitions):
                fs = "NTFS" if p.is_ntfs else "Other"
                entry_str = f"Part {p.index}: {p.name} [{fs}] ({p.size_gb:.2f} GB, LBA {p.start_lba:,})"
                part_values.append(entry_str)
                if p.is_ntfs and ntfs_idx == -1:
                    ntfs_idx = i

            self.part_combo["values"] = part_values
            if part_values:
                self.part_combo.current(ntfs_idx if ntfs_idx != -1 else 0)
                self.log_message(f"Found {len(self.partitions)} partition(s).")
            else:
                self.log_message("No partitions found on drive.", "WARN")
        except Exception as e:
            self.log_message(f"Failed to scan partitions: {e}", "ERROR")

    def start_recovery(self):
        if self.is_running:
            return

        sel_part_idx = self.part_combo.current()
        if sel_part_idx < 0 or sel_part_idx >= len(self.partitions):
            messagebox.showerror("Error", "Please select a valid partition to recover.")
            return

        target_part = self.partitions[sel_part_idx]
        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showerror("Error", "Please choose a destination directory.")
            return

        timeout_ms = self.timeout_var.get()
        include_sys = self.include_sys_var.get()
        run_multi_pass = self.multi_pass_var.get()
        run_carve = self.carve_var.get()

        self.is_running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)

        # Clear tree
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

        self.worker_thread = threading.Thread(
            target=self._recovery_worker,
            args=(target_part, dest_dir, timeout_ms, include_sys, run_multi_pass, run_carve),
            daemon=True,
        )
        self.worker_thread.start()

    def cancel_recovery(self):
        if self.engine:
            self.engine.cancel()
            self.log_message("Cancellation requested. Mapfile progress will be preserved.", "WARN")

    def _recovery_worker(
        self,
        target_part: PartitionInfo,
        dest_dir: str,
        timeout_ms: int,
        include_sys: bool,
        run_multi_pass: bool,
        run_carve: bool,
    ):
        try:
            self.log_message(f"Initializing NTFS Volume at LBA {target_part.start_lba:,}...")
            volume = NTFSVolume(self.reader, target_part.start_lba)

            self.engine = RecoveryEngine(
                volume=volume,
                dest_dir=dest_dir,
                timeout_ms=timeout_ms,
                include_system_files=include_sys,
            )

            # Update initial grid
            self.after(0, lambda: self.sector_grid.update_from_mapfile(self.engine.mapfile))

            # Phase 1: Fast Sweep if enabled
            if run_multi_pass and not self.engine.stats.is_cancelled:
                self.log_message("Starting Phase 1: Fast Sweep & Adaptive Jump...")
                scheduler = MultiPassScheduler(volume=volume, mapfile=self.engine.mapfile, dest_dir=dest_dir, timeout_ms=timeout_ms)
                scheduler.run_phase1_fast_sweep(
                    progress_callback=lambda cur, tot, msg: self.after(0, lambda: self.sector_grid.update_from_mapfile(self.engine.mapfile)),
                )

            # Phase 2: Read MFT & Extract Files
            self.log_message(f"Reading Master File Table ($MFT)...")
            files = read_all_mft_records(volume, max_records=100000)

            user_files = {
                rec_id: f for rec_id, f in files.items()
                if not f.is_directory and f.name and (include_sys or not f.name.startswith("$"))
            }

            self.log_message(f"Found {len(user_files)} recoverable files. Populating tree...")

            # Populate tree view items initially
            self.after(0, self._populate_initial_tree, user_files)

            def on_progress(stats: dict, current_file: NTFSFileInfo, outcome: str):
                self.after(0, self._update_file_recovered, stats, current_file, outcome)

            final_stats = self.engine.run_recovery(user_files, progress_callback=on_progress)

            # Phase 3: Scraping
            if run_multi_pass and not self.engine.stats.is_cancelled:
                self.log_message("Starting Phase 3: Scraping bad sector boundaries...")
                scheduler = MultiPassScheduler(volume=volume, mapfile=self.engine.mapfile, dest_dir=dest_dir, timeout_ms=timeout_ms)
                scheduler.run_phase3_scraping(
                    progress_callback=lambda cur, tot, msg: self.after(0, lambda: self.sector_grid.update_from_mapfile(self.engine.mapfile)),
                )

            # Phase 4: Carving
            if run_carve and not self.engine.stats.is_cancelled:
                self.log_message("Starting signature-based File Carver for orphan files...")
                carved = self.engine.carve_orphan_files(
                    start_lba=target_part.start_lba,
                    sector_count=min(volume.total_sectors, 500000),
                    progress_callback=lambda cur, tot, cnt: self.log_message(f"Carver: {cur:,}/{tot:,} sectors, {cnt} files found"),
                )
                self.log_message(f"Carver finished: {len(carved)} orphan files recovered.")

            st = final_stats.to_dict()
            self.log_message("=" * 50)
            self.log_message(f"RECOVERY FINISHED: {st['recovered_files']} Good, {st['partial_files']} Partial, {st['failed_files']} Failed.")

            self.after(0, lambda: messagebox.showinfo(
                "Recovery Finished",
                f"Recovery finished!\n\n"
                f"Fully Recovered: {st['recovered_files']}\n"
                f"Partial (Zero-filled): {st['partial_files']}\n"
                f"Failed: {st['failed_files']}\n"
                f"Bad Sectors Encountered: {st['bad_sectors']}\n\n"
                f"Manifest saved to:\n{self.engine.manifest_path}"
            ))

        except Exception as e:
            self.log_message(f"Fatal error during recovery: {e}", "ERROR")
            self.after(0, lambda: messagebox.showerror("Recovery Error", f"An error occurred:\n{e}"))

        finally:
            self.is_running = False
            self.after(0, self._reset_ui_after_recovery)

    def _populate_initial_tree(self, user_files: Dict[int, NTFSFileInfo]):
        """Populates the initial file tree entries."""
        self.tree_item_map = {}
        # Limit initial items to first 2000 for UI responsiveness
        for rec_id, f in list(user_files.items())[:2000]:
            size_kb = f.file_size / 1024.0
            size_str = f"{size_kb:3.1f} KB" if size_kb < 1024 else f"{size_kb/1024:3.2f} MB"
            item_id = self.file_tree.insert(
                "",
                tk.END,
                text=f.full_path or f.name,
                values=("PENDING", size_str, "-", "-"),
                tags=("PENDING",),
            )
            self.tree_item_map[rec_id] = item_id

    def _update_file_recovered(self, stats: dict, current_file: NTFSFileInfo, outcome: str):
        pct = stats["percent_complete"]
        self.prog_bar["value"] = pct
        self.lbl_pct.config(text=f"{pct:3.1f}%")

        self.lbl_good_sec.config(text=f"Good Sectors: {stats['good_sectors']:,}")
        self.lbl_bad_sec.config(text=f"Bad Sectors: {stats['bad_sectors']:,}")
        self.lbl_recovered_cnt.config(text=f"Files Recovered: {stats['recovered_files']:,}")
        self.lbl_partial_cnt.config(text=f"Partial Files: {stats['partial_files']:,}")
        self.lbl_failed_cnt.config(text=f"Failed: {stats['failed_files']:,}")

        # Update Grid periodically (every 5 files)
        if stats["processed_files"] % 5 == 0 and self.engine:
            self.sector_grid.update_from_mapfile(self.engine.mapfile)

        # Update file tree row
        rec_id = current_file.record_number
        if hasattr(self, "tree_item_map") and rec_id in self.tree_item_map:
            item_id = self.tree_item_map[rec_id]
            size_kb = current_file.file_size / 1024.0
            size_str = f"{size_kb:3.1f} KB" if size_kb < 1024 else f"{size_kb/1024:3.2f} MB"
            tag = outcome
            status_display = f"🟢 {outcome}" if outcome == "RECOVERED" else (f"🟡 {outcome}" if outcome == "PARTIAL" else f"🔴 {outcome}")
            good_c = len(current_file.data_runs) if outcome == "RECOVERED" else ("Partial" if outcome == "PARTIAL" else "0")
            bad_c = "0" if outcome == "RECOVERED" else ("Bad Sectors" if outcome == "PARTIAL" else "All Bad")

            self.file_tree.item(
                item_id,
                values=(status_display, size_str, good_c, bad_c),
                tags=(tag,),
            )
            # Auto-scroll to current item
            self.file_tree.see(item_id)

    def _reset_ui_after_recovery(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.DISABLED)
        if self.engine:
            self.sector_grid.update_from_mapfile(self.engine.mapfile)

    def on_closing(self):
        if self.is_running and self.engine:
            self.engine.cancel()
        if self.reader:
            self.reader.close()
        self.destroy()


def run_gui(
    initial_drive: Optional[str] = None,
    initial_dest: Optional[str] = None,
    initial_timeout: int = 1000,
    initial_include_sys: bool = False,
    initial_multi_pass: bool = False,
    initial_carve: bool = False,
):
    app = RecoveryApp(
        initial_drive=initial_drive,
        initial_dest=initial_dest,
        initial_timeout=initial_timeout,
        initial_include_sys=initial_include_sys,
        initial_multi_pass=initial_multi_pass,
        initial_carve=initial_carve,
    )
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
