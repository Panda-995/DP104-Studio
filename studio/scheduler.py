"""Time-of-day scenes for the DP104: HID-only actions, so they never touch the CDC.

A rule says "between HH:MM and HH:MM, switch screen mode / set brightness".
Rules are matched in order and the last match wins; ranges may wrap midnight.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

# Screen modes offered to schedules (DIY/custom is deliberately excluded).
MODE_CHOICES = (("打字", 1), ("烟花", 4), ("音乐", 5), ("滚动", 6), ("连接状态", 3), ("熄屏", 0))


def default_config() -> dict:
    return {"enabled": False, "rules": []}


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_config()
    if not isinstance(data, dict):
        return default_config()
    rules = [rule for rule in data.get("rules", [])
             if isinstance(rule, dict) and rule.get("kind") in ("mode", "brightness")]
    return {"enabled": bool(data.get("enabled")), "rules": rules}


def save(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")


def _clock(value) -> dt.time | None:
    try:
        parts = str(value).strip().split(":")
        return dt.time(int(parts[0]) % 24, int(parts[1]) % 60)
    except (ValueError, IndexError, TypeError):
        return None


def matches(rule: dict, now: dt.time) -> bool:
    start, end = _clock(rule.get("start")), _clock(rule.get("end"))
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def active_rule(config: dict, now: dt.datetime) -> dict | None:
    if not config.get("enabled"):
        return None
    chosen = None
    for rule in config.get("rules", []):
        if matches(rule, now.time()):
            chosen = rule
    return chosen


def describe(rule: dict) -> str:
    if rule.get("kind") == "brightness":
        return f"亮度 {rule.get('value')}"
    name = next((n for n, v in MODE_CHOICES if str(v) == str(rule.get("value"))), rule.get("value"))
    return f"{name}模式"


def rule_line(rule: dict) -> str:
    return f"{rule.get('start')} - {rule.get('end')}   {describe(rule)}"
