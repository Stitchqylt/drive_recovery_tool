"""
multipass_scheduler.py - Multi-Pass Adaptive Jump & Scraping Scheduler

Implements a 4-phase industrial recovery strategy:
1. Fast Sweep & Adaptive Jump: Rapidly harvests healthy data and jumps past bad zones.
2. Targeted File Extraction: Recovers file streams prioritized by MFT metadata.
3. Scraping & Trimming: Bisects skipped zones sector-by-sector to salvage every good byte.
4. Raw Carving: Extracts orphan files from unallocated / damaged filesystem areas.
"""

import time
from typing import Dict, List, Optional, Callable, Any
from ntfs import NTFSVolume, NTFSFileInfo, DataRun
from recovery_map import (
    RecoveryMapFile,
    STATE_UNTOUCHED,
    STATE_RECOVERED,
    STATE_SKIPPED,
    STATE_BAD,
    STATE_SCRAPED,
)
from monitor import WatchdogDiskReader
from carver import FileCarver


class MultiPassScheduler:
    """
    Coordinates multi-phase recovery passes across the target volume.
    """
    def __init__(
        self,
        volume: NTFSVolume,
        mapfile: RecoveryMapFile,
        dest_dir: str,
        timeout_ms: int = 1000,
    ):
        self.volume = volume
        self.mapfile = mapfile
        self.reader = WatchdogDiskReader(volume.reader)
        self.dest_dir = dest_dir
        self.timeout_ms = timeout_ms
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run_phase1_fast_sweep(
        self,
        chunk_clusters: int = 32,
        initial_jump_clusters: int = 64,
        max_jump_clusters: int = 512,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ):
        """
        Phase 1: Fast Sweep & Adaptive Jump.
        Reads large chunks. On failure, skips forward and doubles jump size.
        """
        total_clusters = self.volume.total_sectors // self.volume.sectors_per_cluster
        curr_cluster = 0
        current_jump = initial_jump_clusters

        while curr_cluster < total_clusters:
            if self.is_cancelled:
                break

            to_read_clusters = min(chunk_clusters, total_clusters - curr_cluster)
            start_lba = self.volume.lcn_to_sector(curr_cluster)
            sector_count = to_read_clusters * self.volume.sectors_per_cluster

            # Check if already recovered from previous session
            if self.mapfile.is_range_recovered(start_lba, sector_count):
                curr_cluster += to_read_clusters
                continue

            chunk_data = self.reader.read_sectors(start_lba, sector_count, timeout_ms=self.timeout_ms)

            if chunk_data and len(chunk_data) == sector_count * self.volume.bytes_per_sector:
                # Success
                self.mapfile.mark_range(start_lba, sector_count, STATE_RECOVERED)
                curr_cluster += to_read_clusters
                current_jump = initial_jump_clusters  # Reset jump
            else:
                # Read failed or timed out! Mark chunk as SKIPPED and jump ahead
                self.mapfile.mark_range(start_lba, sector_count, STATE_SKIPPED)
                curr_cluster += to_read_clusters + current_jump
                current_jump = min(max_jump_clusters, current_jump * 2)

            if progress_callback and curr_cluster % 500 == 0:
                progress_callback(curr_cluster, total_clusters, "Phase 1: Fast Sweep")

        self.mapfile.save()

    def run_phase3_scraping(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ):
        """
        Phase 3: Scraping.
        Finds all remaining SKIPPED intervals and tests sector-by-sector.
        """
        skipped_intervals = [iv for iv in self.mapfile.intervals if iv.state == STATE_SKIPPED]
        total_skipped_sectors = sum(iv.sector_count for iv in skipped_intervals)
        processed = 0

        for iv in skipped_intervals:
            if self.is_cancelled:
                break

            for s_offset in range(iv.sector_count):
                if self.is_cancelled:
                    break

                sector_lba = iv.start_lba + s_offset
                sec_data = self.reader.read_sectors(sector_lba, 1, timeout_ms=self.timeout_ms)

                if sec_data:
                    self.mapfile.mark_range(sector_lba, 1, STATE_SCRAPED)
                else:
                    self.mapfile.mark_range(sector_lba, 1, STATE_BAD)

                processed += 1
                if progress_callback and processed % 100 == 0:
                    progress_callback(processed, total_skipped_sectors, "Phase 3: Scraping Bad Zones")

        self.mapfile.save()
