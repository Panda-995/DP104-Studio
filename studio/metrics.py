"""Device-specific key counts from the VIA switch matrix; no key text is stored."""

from __future__ import annotations

import collections
import datetime as dt
import json
import threading
import time
from pathlib import Path


class TypingMetrics:
    MATRIX_ROWS = 7
    MATRIX_COLS = 16
    MATRIX_BYTES = MATRIX_ROWS * 2

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.day = dt.date.today().isoformat()
        self.keys_today = 0
        self.previous = b""
        self.recent = collections.deque()
        self.cells: dict[tuple[int, int], int] = {}
        self.matrix_cols = self.MATRIX_COLS
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("day") == self.day:
                self.keys_today = max(0, int(data.get("keys", 0)))
                for entry in data.get("cells", []):
                    if not isinstance(entry, list) or len(entry) != 3:
                        continue
                    row, col, count = map(int, entry)
                    if 0 <= row < self.MATRIX_ROWS and 0 <= col < self.MATRIX_COLS and count > 0:
                        self.cells[(row, col)] = count
        except (OSError, ValueError, TypeError):
            pass

    def reset_matrix(self) -> None:
        with self.lock:
            self.previous = b""

    def observe(self, matrix: bytes) -> None:
        now = time.time()
        with self.lock:
            self._roll_day()
            # VIA returns 7 rows of 16 bits, high byte first for each row.
            matrix = matrix[:self.MATRIX_BYTES].ljust(self.MATRIX_BYTES, b"\0")
            if not self.previous:
                self.previous = matrix
                return
            old = self.previous
            added = 0
            for index, (new, prev) in enumerate(zip(matrix, old)):
                diff = new & ~prev & 0xFF
                while diff:
                    bit = (diff & -diff).bit_length() - 1
                    row = index // 2
                    col = (1 - index % 2) * 8 + bit
                    self.cells[(row, col)] = self.cells.get((row, col), 0) + 1
                    diff &= diff - 1
                    added += 1
            self.previous = matrix
            if added:
                self.keys_today += added
                self.recent.extend([now] * added)
            self._trim(now)

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            self._roll_day()
            self._trim(time.time())
            per_minute = len(self.recent)
            return {"keys_today": self.keys_today,
                    "keys_per_minute": per_minute,
                    "wpm": round(per_minute / 5)}

    def cells_snapshot(self) -> tuple[dict[tuple[int, int], int], int]:
        with self.lock:
            return dict(self.cells), self.matrix_cols

    def save(self) -> None:
        with self.lock:
            self._roll_day()
            payload = {"day": self.day, "keys": self.keys_today,
                       "cells": [[row, col, count] for (row, col), count in sorted(self.cells.items())]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _roll_day(self) -> None:
        today = dt.date.today().isoformat()
        if today != self.day:
            self.day = today
            self.keys_today = 0
            self.recent.clear()
            self.previous = b""
            self.cells.clear()

    def _trim(self, now: float) -> None:
        while self.recent and now - self.recent[0] > 60:
            self.recent.popleft()
