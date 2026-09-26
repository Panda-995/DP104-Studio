"""Import local artwork into the DP104's 8 x 24 HSV animation format."""

from __future__ import annotations

import colorsys
import bisect
import re
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from .effects import COLS, ROWS, MAX_FRAMES

MAX_SOURCE_FRAMES = 512
MAX_PIXELS = 16_000_000


def rgb_to_hsv_pixel(red: int, green: int, blue: int) -> bytes:
    """Apply the same low-saturation/low-value workarounds as AI artwork."""
    if max(red, green, blue) < 12:
        return b"\0\0\0"
    hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
    if saturation < 0.16:
        hue = 0.13 if value >= 0.5 else 0.60
        saturation = 0.35
    return bytes((round(hue * 255) & 255, round(saturation * 255),
                  round(max(0.10, value) * 255)))


def rgb_hex_to_hsv_pixel(color: str) -> bytes:
    if len(color) != 7 or not color.startswith("#"):
        raise ValueError("颜色需要使用 #RRGGBB 格式")
    try:
        rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError as exc:
        raise ValueError("颜色需要使用 #RRGGBB 格式") from exc
    return rgb_to_hsv_pixel(*rgb)


def _fit(image: Image.Image, mode: str) -> Image.Image:
    image = image.convert("RGBA")
    if mode == "裁切填满":
        result = ImageOps.fit(image, (COLS, ROWS), method=Image.Resampling.NEAREST,
                              centering=(0.5, 0.5))
    elif mode == "拉伸填满":
        result = image.resize((COLS, ROWS), Image.Resampling.NEAREST)
    elif mode == "完整显示":
        result = Image.new("RGBA", (COLS, ROWS), (0, 0, 0, 255))
        fitted = ImageOps.contain(image, (COLS, ROWS), method=Image.Resampling.NEAREST)
        result.alpha_composite(fitted, ((COLS - fitted.width) // 2,
                                        (ROWS - fitted.height) // 2))
    else:
        raise ValueError("未知图片适应方式")
    background = Image.new("RGBA", (COLS, ROWS), (0, 0, 0, 255))
    background.alpha_composite(result)
    return background.convert("RGB")


def image_to_frame(image: Image.Image, mode: str = "完整显示") -> bytes:
    fitted = _fit(image, mode)
    pixels = bytearray()
    data = fitted.get_flattened_data() if hasattr(fitted, "get_flattened_data") else fitted.getdata()
    for red, green, blue in data:
        pixels.extend(rgb_to_hsv_pixel(red, green, blue))
    return bytes(pixels)


def _sample_indices(length: int) -> list[int]:
    if length <= MAX_FRAMES:
        return list(range(length))
    return [round(index * (length - 1) / (MAX_FRAMES - 1)) for index in range(MAX_FRAMES)]


def _timed_indices(durations: list[int]) -> tuple[list[int], int]:
    """Resample GIF display times onto the keyboard's fixed-FPS loop."""
    elapsed = []
    total = 0
    for duration in durations:
        total += duration
        elapsed.append(total)
    target_fps = min(30, max(1, round(1000 / min(durations))))
    count = min(MAX_FRAMES, max(2, round(total * target_fps / 1000)))
    fps = min(30, max(1, round(count * 1000 / total)))
    indices = [min(len(durations) - 1,
                   bisect.bisect_right(elapsed, (index + 0.5) * total / count))
               for index in range(count)]
    return indices, fps


def _natural_name(path: Path):
    return [int(part) if part.isdecimal() else part.casefold()
            for part in re.split(r"(\d+)", path.name)]


def import_images(paths: list[Path], mode: str = "完整显示") -> tuple[list[bytes], int]:
    """Open one animated file or an ordered image sequence, keeping one loop."""
    if not paths:
        raise ValueError("请先选择图片")
    if mode not in ("完整显示", "裁切填满", "拉伸填满"):
        raise ValueError("未知图片适应方式")
    if len(paths) > MAX_SOURCE_FRAMES:
        raise ValueError("图片序列最多选择 512 张")
    paths = sorted((Path(path) for path in paths), key=_natural_name)
    samples: list[bytes] = []
    durations: list[int] = []
    try:
        for path in paths:
            with Image.open(path) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError(f"图片尺寸过大：{path.name}")
                count = getattr(image, "n_frames", 1)
                if count > MAX_SOURCE_FRAMES:
                    raise ValueError(f"动画超过 512 帧：{path.name}")
                for frame in ImageSequence.Iterator(image):
                    # Keep only the 576-byte LED frame, not the full decoded
                    # source image for every GIF frame.
                    samples.append(image_to_frame(frame, mode))
                    durations.append(max(20, int(frame.info.get("duration", image.info.get("duration", 125)) or 125)))
                    if len(samples) > MAX_SOURCE_FRAMES:
                        raise ValueError("图片序列总帧数超过 512")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"无法读取图片：{exc}") from exc
    if not samples:
        raise ValueError("图片中没有可用画面")
    if len(paths) == 1 and len(samples) > 1:
        indices, fps = _timed_indices(durations)
    else:
        indices = _sample_indices(len(samples))
        fps = 8
    frames = [samples[index] for index in indices]
    return frames, fps


def import_tabml(path: Path) -> tuple[list[bytes], int]:
    """Read the official tabml RGB format; never treat file data as commands."""
    data = Path(path).read_bytes()
    if len(data) < 32 or data[:5] != b"tabml":
        raise ValueError("不是支持的官方 tabml 动画文件")
    declared_count = data[5]
    fps, rows, cols = data[6:9]
    if (rows, cols) != (ROWS, COLS) or not 1 <= fps <= 30:
        raise ValueError("官方动画的尺寸或帧率与 DP104 不符")
    if any(data[9:32]):
        raise ValueError("官方动画含有未识别的头部字段")
    payload = data[32:]
    frame_bytes = ROWS * COLS * 3
    if not payload or len(payload) % frame_bytes:
        raise ValueError("官方动画帧数据不完整")
    count = len(payload) // frame_bytes
    if count != declared_count:
        raise ValueError("官方动画头部帧数与实际数据不一致")
    if count > MAX_FRAMES:
        raise ValueError(f"官方动画有 {count} 帧；当前程序最多支持 {MAX_FRAMES} 帧")
    frames = []
    for offset in range(0, len(payload), frame_bytes):
        rgb_frame = payload[offset:offset + frame_bytes]
        hsv_frame = bytearray()
        for pixel in range(0, frame_bytes, 3):
            hsv_frame.extend(rgb_to_hsv_pixel(*rgb_frame[pixel:pixel + 3]))
        frames.append(bytes(hsv_frame))
    return frames, fps
