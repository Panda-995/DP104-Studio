"""Looping 8 × 24 HSV animations stored and played by the DP104 firmware."""

from __future__ import annotations

import math

ROWS, COLS = 8, 24
# The official VIA web driver defaults to 200 frames for this model (its
# DP104 matrixLighting config has no maxFrame override). Physical verification
# is tracked separately from this product-facing limit.
MAX_FRAMES = 200
EFFECTS = ("火焰", "心跳", "心电图", "雨滴", "扫描光", "波纹", "彩虹流动", "星河",
           "小猫眨眼", "太空飞船", "音符跳跳", "樱花飘落")

# One continuous P–QRS–T trace; adjacent columns are connected below.
ECG = (4, 4, 4, 3, 3, 4, 4, 5, 0, 6, 4, 4,
       4, 4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4)

HEART_SMALL = (
    ".##...##.",
    "####.####",
    "#########",
    "#########",
    ".#######.",
    "..#####..",
    "...###...",
    "....#....",
)
HEART_LARGE = (
    "..##...##..",
    ".####.####.",
    "###########",
    "###########",
    ".#########.",
    "..#######..",
    "...#####...",
    ".....#.....",
)


def _pixel(name: str, x: int, y: int, phase: int, count: int) -> tuple[float, float, float]:
    t = phase / count
    if name == "火焰":
        flow = math.sin(x * 0.58 - phase * 0.85) + math.sin(x * 1.31 + phase * 0.7)
        height = max(2, min(7, round(4.5 + flow)))
        if y >= ROWS - height:
            depth = (y - (ROWS - height)) / max(1, height - 1)
            return 0.11 - depth * 0.09, 0.96, 0.55 + depth * 0.43
        if y == ROWS - height - 1 and (x * 7 + phase * 3) % 11 == 0:
            return 0.12, 0.9, 0.48
        return 0, 0, 0
    if name == "心跳":
        beat = phase * 8 // count
        pattern = HEART_LARGE if beat in (2, 3) else HEART_SMALL
        left = (COLS - len(pattern[0])) // 2
        on = left <= x < left + len(pattern[0]) and pattern[y][x - left] == "#"
        halo = beat in (2, 3) and y in (1, 2, 3) and x in (left - 1, left + len(pattern[0]))
        return 0.96, 0.88, 1.0 if on else 0.24 if halo else 0
    if name == "心电图":
        step = phase * COLS // count
        top, bottom = sorted((ECG[(x - 1) % COLS], ECG[x]))
        if top <= y <= bottom:
            age = (step - x) % COLS
            value = 1.0 if age == 0 else 0.84 if age < 4 else 0.47
            return 0.43, 0.9, value
        if x == step and abs(y - ECG[x]) == 1:
            return 0.43, 0.8, 0.28
        return 0, 0, 0
    if name == "雨滴":
        for column in (1, 5, 9, 13, 17, 21):
            drift = (column + phase // 3) % COLS
            head = (phase * 2 + column * 3) % (ROWS + 5) - 2
            if x == drift and 0 <= head - y <= 2:
                return 0.56, 0.82, (1.0, 0.52, 0.24)[head - y]
            if y == ROWS - 1 and abs(x - drift) <= 1 and head == ROWS - 1:
                return 0.56, 0.76, 0.55
        return 0, 0, 0
    if name == "扫描光":
        center = phase * COLS // count
        distance = min(abs(x - center), COLS - abs(x - center))
        if distance <= 3:
            return 0.51 + y / 80, 0.86, (1.0, 0.68, 0.38, 0.18)[distance]
        return 0, 0, 0
    if name == "波纹":
        distance = math.hypot((x - 11.5) / 2.2, y - 3.5)
        radius = phase * 8 / count
        gap = abs(distance - radius)
        if gap < 0.85:
            return 0.55, 0.9, max(0.24, 1.0 - gap * 0.8)
        if distance < 0.75:
            return 0.57, 0.8, 0.44
        return 0, 0, 0
    if name == "彩虹流动":
        hue = (x / COLS + y / 32 + t) % 1
        brightness = 0.58 + 0.38 * (1 + math.sin((x + y * 2) * 0.56 - t * 2 * math.pi)) / 2
        return hue, 0.94, brightness
    if name == "星河":
        star = ((x * 17 + y * 29) % 43) < 2
        if star:
            twinkle = 0.55 + 0.45 * (1 + math.sin(phase * math.pi / 4 + x)) / 2
            return 0.59, 0.34, twinkle
        lane = abs(y - 3.5 - 1.7 * math.sin(x / 4 - t * 2 * math.pi))
        if lane < 1.3:
            return 0.67, 0.76, 0.22 + (1.3 - lane) * 0.15
        return 0, 0, 0
    if name == "小猫眨眼":
        face = ((y == 1 and x in (7, 8, 16, 17)) or
                (y == 2 and (x in (6, 9, 15, 18) or 10 <= x <= 14)) or
                (y in (3, 4, 5) and x in (6, 18)) or
                (y == 6 and 7 <= x <= 17))
        if face:
            return 0.09, 0.78, 0.87
        if y == 4 and x in (10, 14):
            return 0.12, 0.7, 0.92 if phase * 8 // count not in (3, 4) else 0.27
        if y == 5 and x == 12:
            return 0.96, 0.8, 0.96
        if y == 5 and x in (10, 14):
            return 0.09, 0.78, 0.62
        if y == 4 and x in (3, 4, 20, 21):
            return 0.09, 0.7, 0.48
        return 0, 0, 0
    if name == "太空飞船":
        ship_x = (phase * (COLS + 8) // count) - 4
        local = x - ship_x
        if (y == 3 and 0 <= local <= 5) or (y == 2 and 1 <= local <= 3) or (y == 4 and 1 <= local <= 3):
            return 0.55, 0.86, 0.95 if local >= 4 else 0.7
        if y == 3 and local in (-2, -1):
            return 0.06, 1.0, 0.85 if local == -1 else 0.36
        if (x * 13 + y * 19) % 47 == 0:
            return 0.63, 0.42, 0.4
        return 0, 0, 0
    if name == "音符跳跳":
        for index, base in enumerate((4, 11, 18)):
            bounce = (phase + index * 2) * 8 // count
            head_y = (4, 3, 2, 3, 4, 4, 4, 4)[bounce % 8]
            if (x in (base, base + 1) and y == head_y + 1) or (x == base + 1 and head_y - 2 <= y <= head_y):
                return (0.86 + index * 0.17) % 1, 0.84, 0.95
            if x == base + 2 and y == head_y - 2:
                return (0.86 + index * 0.17) % 1, 0.84, 0.75
        return 0, 0, 0
    if name == "樱花飘落":
        for index, start in enumerate((2, 8, 14, 20)):
            cy = (phase + index * 3) % (ROWS + 3) - 1
            cx = (start + phase // 2 + int(math.sin((phase + index) * 0.8))) % COLS
            gap = abs(x - cx) + abs(y - cy)
            if gap <= 1:
                return 0.94, 0.65, 0.96 if gap == 0 else 0.66
        return 0, 0, 0
    raise ValueError("未知特效")


def make_frames(name: str, count: int = 8) -> list[bytes]:
    if name not in EFFECTS or not 1 <= count <= MAX_FRAMES:
        raise ValueError("未知特效或帧数无效")
    frames = []
    for phase in range(count):
        data = bytearray()
        for y in range(ROWS):
            for x in range(COLS):
                hue, saturation, value = _pixel(name, x, y, phase, count)
                data.extend((round(hue * 255) & 255,
                             round(saturation * 255),
                             round(min(1.0, max(0.0, value)) * 255)))
        frames.append(bytes(data))
    return frames


def dim_frames(frames: list[bytes], brightness: int) -> list[bytes]:
    """Scale uploaded DIY pixels; the firmware's channel-26 brightness has no visible effect."""
    level = max(0, min(255, int(brightness)))
    if level == 255:
        return frames
    result = []
    for source in frames:
        frame = bytearray(source)
        for offset in range(2, len(frame), 3):
            value = round(frame[offset] * level / 255)
            frame[offset] = value if value >= 20 else 0
        result.append(bytes(frame))
    return result
