"""Typing milestones: celebrate every N-thousandth keypress with one frame push.

Event-driven only: when the counter crosses a fresh multiple of the step, the
app uploads a short celebration animation once. The state file keeps the last
fired value (plus the settings) so restarting the app never repeats an old
celebration and survives configuration across days.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class Milestones:
    def __init__(self, path: Path, step: int = 5000, enabled: bool = True):
        self.path = path
        self.step = max(500, int(step))
        self.enabled = bool(enabled)
        self.day = dt.date.today().isoformat()
        self.last = -1  # -1 = needs calibration against today's counter
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.step = max(500, int(data.get("step", self.step)))
        self.enabled = bool(data.get("enabled", self.enabled))
        if data.get("day") == self.day:
            self.last = max(0, int(data.get("last", 0)))

    def _store(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"day": self.day, "last": self.last,
                 "step": self.step, "enabled": self.enabled}),
                encoding="utf-8")
        except OSError:
            pass

    def configure(self, step: int, enabled: bool) -> None:
        self.step = max(500, int(step))
        self.enabled = bool(enabled)
        self._store()

    def _roll(self) -> None:
        today = dt.date.today().isoformat()
        if today != self.day:
            self.day = today
            self.last = 0
            self._store()

    def sync(self, keys_today: int) -> int | None:
        """Return the key count if a fresh milestone was just crossed, else None.

        Polling gaps may skip several multiples; the celebration fires once per
        sync when at least one new multiple has been crossed.
        """
        self._roll()
        floor = (keys_today // self.step) * self.step
        if self.last < 0:           # first observation today: adopt, never fire
            self.last = floor
            self._store()
            return None
        if keys_today < self.last:  # counter went backwards (reset): re-adopt
            self.last = floor
            self._store()
            return None
        if floor > self.last:
            self.last = floor
            self._store()
            return keys_today
        return None

    def remaining(self, keys_today: int) -> int:
        return max(0, self.last + self.step - keys_today)
