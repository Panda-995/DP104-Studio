"""User preset library: DP104 animations stored as JSON files next to the app data.

Each preset is one file so renaming and deleting stay simple, and exporting
means copying the file itself.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .effects import MAX_FRAMES, COLS, ROWS

SAFE_NAME = re.compile(r"[\\/:*?\"<>|]+")


def library_dir(override: Path | None = None) -> Path:
    if override is not None:
        return override
    base = Path(__import__("os").environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "DP104Studio" / "presets"


def _file_for(name: str, directory: Path) -> Path:
    clean = SAFE_NAME.sub("_", name).strip() or "未命名"
    return directory / f"{clean}.json"


def save(name: str, frames: list[bytes], fps: int = 8, source: str = "manual",
         directory: Path | None = None) -> str:
    if not 1 <= fps <= 30:
        raise ValueError("帧率超出 1-30")
    if not frames or len(frames) > MAX_FRAMES:
        raise ValueError(f"帧数需在 1-{MAX_FRAMES} 之间")
    if any(len(frame) != ROWS * COLS * 3 for frame in frames):
        raise ValueError("每帧必须是 8×24 HSV 数据")
    directory = directory or library_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "fps": fps, "source": source,
               "created": dt.datetime.now().isoformat(timespec="seconds"),
               "frames": [frame.hex() for frame in frames]}
    _file_for(name, directory).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return name


def load(name: str, directory: Path | None = None) -> tuple[list[bytes], int]:
    path = _file_for(name, directory or library_dir())
    data = json.loads(path.read_text(encoding="utf-8"))
    frames = [bytes.fromhex(chunk) for chunk in data["frames"]]
    return frames, int(data.get("fps", 8))


def delete(name: str, directory: Path | None = None) -> None:
    _file_for(name, directory or library_dir()).unlink(missing_ok=True)


def rename(old: str, new: str, directory: Path | None = None) -> str:
    directory = directory or library_dir()
    frames, fps = load(old, directory)
    data = json.loads(_file_for(old, directory).read_text(encoding="utf-8"))
    data["name"] = new
    save(new, frames, fps, data.get("source", "manual"), directory)
    delete(old, directory)
    return new


def names(directory: Path | None = None) -> list[str]:
    directory = directory or library_dir()
    if not directory.exists():
        return []
    found = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(str(data.get("name") or path.stem))
        except (OSError, ValueError):
            continue
    return sorted(set(found))


def export_to(name: str, target: Path, directory: Path | None = None) -> None:
    path = _file_for(name, directory or library_dir())
    target.write_bytes(path.read_bytes())


def import_from(source: Path, directory: Path | None = None) -> str:
    data = json.loads(source.read_text(encoding="utf-8"))
    name = str(data.get("name") or source.stem)
    frames = [bytes.fromhex(chunk) for chunk in data["frames"]]
    save(name, frames, int(data.get("fps", 8)), str(data.get("source", "imported")),
         directory)
    return name
