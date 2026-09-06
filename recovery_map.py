"""
recovery_map.py - Persistent, Crash-Resilient Recovery Mapfile Journal

Maintains a contiguous block-level map of the entire drive / partition,
tracking every sector's exact recovery state (UNTOUCHED, RECOVERED, SKIPPED, BAD, SCRAPED).
Enables seamless session pause, power-cycle survival, and instant resumability.
"""

import os
import bisect
from typing import List, Tuple, Dict, Any, Optional

# Block States
STATE_UNTOUCHED = 0   # '?' Not yet read
STATE_RECOVERED = 1   # '+' Successfully read (Good)
STATE_SKIPPED = 2     # '*' Skipped due to proximity to bad sector / slow read
STATE_BAD = 3         # '-' Unreadable / Timeout (Zero-filled)
STATE_SCRAPED = 4     # '/' Scraped during trimming phase

STATE_SYMBOLS = {
    STATE_UNTOUCHED: "?",
    STATE_RECOVERED: "+",
    STATE_SKIPPED: "*",
    STATE_BAD: "-",
    STATE_SCRAPED: "/",
}

SYMBOL_TO_STATE = {v: k for k, v in STATE_SYMBOLS.items()}


class MapInterval:
    """Represents a contiguous range of sectors with uniform state."""
    __slots__ = ("start_lba", "sector_count", "state")

    def __init__(self, start_lba: int, sector_count: int, state: int):
        self.start_lba = start_lba
        self.sector_count = sector_count
        self.state = state

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.sector_count

    def __repr__(self) -> str:
        return f"[{self.start_lba:,}..{self.end_lba - 1:,} ({self.sector_count:,}) {STATE_SYMBOLS.get(self.state, '?')}]"


class RecoveryMapFile:
    """
    Manages an interval-tree style list of non-overlapping, sorted MapIntervals.
    Flushes atomically to disk.
    """
    def __init__(self, filepath: str, total_sectors: int = 0):
        self.filepath = os.path.abspath(filepath)
        self.total_sectors = total_sectors
        self.intervals: List[MapInterval] = []

        if os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 0:
            self.load()
        elif total_sectors > 0:
            self.intervals = [MapInterval(0, total_sectors, STATE_UNTOUCHED)]
            self.save()

    def load(self):
        """Loads mapfile from disk."""
        self.intervals = []
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    start_lba = int(parts[0], 0)
                    sector_count = int(parts[1], 0)
                    symbol = parts[2]
                    state = SYMBOL_TO_STATE.get(symbol, STATE_UNTOUCHED)
                    self.intervals.append(MapInterval(start_lba, sector_count, state))

        if self.intervals:
            self.total_sectors = max(self.total_sectors, self.intervals[-1].end_lba)

    def save(self):
        """Atomically saves mapfile to disk."""
        tmp_path = self.filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("# NTFS Raw Drive Recovery Mapfile\n")
            f.write(f"# Total Sectors: {self.total_sectors:,}\n")
            f.write("# Format: <Start_LBA> <Sector_Count> <State_Symbol (?=Untouched, +=Good, *=Skipped, -=Bad, /=Scraped)>\n")
            for iv in self.intervals:
                symbol = STATE_SYMBOLS.get(iv.state, "?")
                f.write(f"0x{iv.start_lba:X} 0x{iv.sector_count:X} {symbol}\n")

        # Atomic rename
        if os.path.exists(self.filepath):
            try:
                os.replace(tmp_path, self.filepath)
            except Exception:
                os.remove(self.filepath)
                os.rename(tmp_path, self.filepath)
        else:
            os.rename(tmp_path, self.filepath)

    def mark_range(self, start_lba: int, count: int, new_state: int):
        """
        Updates the state for range [start_lba, start_lba + count).
        Splits and merges intervals as necessary.
        """
        if count <= 0:
            return

        end_lba = start_lba + count
        new_intervals: List[MapInterval] = []

        for iv in self.intervals:
            # Case 1: Interval is completely before target range
            if iv.end_lba <= start_lba:
                new_intervals.append(iv)
            # Case 2: Interval is completely after target range
            elif iv.start_lba >= end_lba:
                new_intervals.append(iv)
            # Case 3: Interval overlaps with target range
            else:
                # Left slice if interval starts before target range
                if iv.start_lba < start_lba:
                    new_intervals.append(MapInterval(iv.start_lba, start_lba - iv.start_lba, iv.state))
                # Right slice if interval ends after target range
                if iv.end_lba > end_lba:
                    new_intervals.append(MapInterval(end_lba, iv.end_lba - end_lba, iv.state))

        # Insert target interval
        new_intervals.append(MapInterval(start_lba, count, new_state))

        # Sort intervals by start_lba
        new_intervals.sort(key=lambda x: x.start_lba)

        # Merge adjacent intervals with identical state
        merged: List[MapInterval] = []
        for iv in new_intervals:
            if not merged:
                merged.append(iv)
            else:
                prev = merged[-1]
                if prev.end_lba == iv.start_lba and prev.state == iv.state:
                    prev.sector_count += iv.sector_count
                else:
                    merged.append(iv)

        self.intervals = merged

    def is_range_recovered(self, start_lba: int, count: int) -> bool:
        """Returns True if the entire range is already marked RECOVERED."""
        end_lba = start_lba + count
        for iv in self.intervals:
            if iv.start_lba <= start_lba and iv.end_lba >= end_lba:
                return iv.state == STATE_RECOVERED
        return False

    def get_stats(self) -> Dict[str, int]:
        """Returns total sector counts per state."""
        counts = {
            "untouched": 0,
            "recovered": 0,
            "skipped": 0,
            "bad": 0,
            "scraped": 0,
        }
        for iv in self.intervals:
            if iv.state == STATE_UNTOUCHED:
                counts["untouched"] += iv.sector_count
            elif iv.state == STATE_RECOVERED:
                counts["recovered"] += iv.sector_count
            elif iv.state == STATE_SKIPPED:
                counts["skipped"] += iv.sector_count
            elif iv.state == STATE_BAD:
                counts["bad"] += iv.sector_count
            elif iv.state == STATE_SCRAPED:
                counts["scraped"] += iv.sector_count
        return counts
