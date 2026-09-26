"""Optional brightness/color tweening between already valid LED frames."""

from __future__ import annotations

import colorsys

from .effects import COLS, ROWS, MAX_FRAMES


def tween_frames(frames: list[bytes], fps: int, factor: int) -> tuple[list[bytes], int]:
    """Add temporal blends without claiming any extra physical LED resolution."""
    if factor not in (1, 2, 3):
        raise ValueError("补帧倍数只能是 1、2 或 3")
    if not frames or len(frames) * factor > MAX_FRAMES or any(
            len(frame) != ROWS * COLS * 3 for frame in frames):
        raise ValueError("补帧后动画超过设备设置上限或帧格式不正确")
    if not 1 <= fps <= 30 or fps * factor > 30:
        raise ValueError("帧率需在 1-30 之间")
    if factor == 1 or len(frames) == 1:
        return frames, fps
    output = []
    for index, first in enumerate(frames):
        second = frames[(index + 1) % len(frames)]
        output.append(first)
        for step in range(1, factor):
            weight = step / factor
            frame = bytearray()
            for offset in range(0, len(first), 3):
                a = first[offset:offset + 3]
                b = second[offset:offset + 3]
                if a == b:
                    frame.extend(a)
                    continue
                rgb_a = colorsys.hsv_to_rgb(a[0] / 255, a[1] / 255, a[2] / 255)
                rgb_b = colorsys.hsv_to_rgb(b[0] / 255, b[1] / 255, b[2] / 255)
                rgb = tuple(rgb_a[channel] * (1 - weight) + rgb_b[channel] * weight
                            for channel in range(3))
                hue, sat, val = colorsys.rgb_to_hsv(*rgb)
                if val < 12 / 255:
                    frame.extend(b"\0\0\0")
                    continue
                if sat < 0.16:
                    hue = 0.13 if val >= 0.5 else 0.60
                    sat = 0.35
                frame.extend((round(hue * 255) & 255, round(sat * 255),
                              round(max(0.10, val) * 255)))
            output.append(bytes(frame))
    return output, fps * factor
