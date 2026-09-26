"""Pixel text rendering for DP104 screens: milestone cards and shared font."""

from __future__ import annotations

from .effects import COLS, ROWS

FONT = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", "..#", "..#", "..#"),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "C": ("###", "#..", "#..", "#..", "###"),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "K": ("#.#", "##.", "#..", "##.", "#.#"),
    "M": ("#.#", "###", "###", "#.#", "#.#"),
    "P": ("##.", "#.#", "##.", "#..", "#.."),
    "S": ("###", "#..", "###", "..#", "###"),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "W": ("#.#", "#.#", "###", "###", "#.#"),
    ":": ("...", ".#.", "...", ".#.", "..."),
    "%": ("#.#", "..#", ".#.", "#..", "#.#"),
    "-": ("...", "...", "###", "...", "..."),
    " ": ("...", "...", "...", "...", "..."),
}


def compact_number(number: float) -> str:
    number = max(0, float(number))
    if number >= 1000000:
        return f"{round(number / 1000000)}M"
    if number >= 1000:
        return f"{round(number / 1000)}K"
    return str(round(number))


def render_text(text: str, color=(145, 215, 250)) -> bytes:
    text = text.upper()
    width = len(text) * 4 - 1
    if width > COLS or any(char not in FONT for char in text):
        raise ValueError("内容超出 8×24 点阵范围")
    pixels = bytearray(ROWS * COLS * 3)
    start = (COLS - width) // 2
    for index, char in enumerate(text):
        for y, row in enumerate(FONT[char]):
            for x, mark in enumerate(row):
                if mark == "#":
                    offset = ((y + 1) * COLS + start + index * 4 + x) * 3
                    pixels[offset:offset + 3] = bytes(color)
    return bytes(pixels)


def make_milestone_frames(keys: int, color=(255, 170, 90)) -> list[bytes]:
    """Eight seamless frames: milestone number with travelling corner sparks."""
    text = compact_number(keys)
    base = render_text(text, color)
    dim = tuple(round(channel * 0.35) for channel in color)
    frames = []
    for phase in range(8):
        frame = bytearray(base)
        # gentle pulse of the digits themselves
        strength = 1.0 if phase in (1, 2, 5, 6) else 0.55
        if phase in (0, 4):
            for index in range(0, len(frame), 3):
                if frame[index:index + 3] == bytes(color):
                    frame[index:index + 3] = bytes(dim)
        # two sparks orbiting the border, opposite directions
        border = [(0, x) for x in range(COLS)] + [(ROWS - 1, x) for x in range(COLS - 2, 0, -1)]
        for direction in (0, 4):
            y, x = border[(phase * 3 + direction * 7) % len(border)]
            offset = (y * COLS + x) * 3
            frame[offset:offset + 3] = bytes((255, 240, 190))
        frames.append(bytes(frame))
    return frames
