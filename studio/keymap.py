"""Physical DP104 key positions for the 7×16 VIA switch matrix.

The coordinate mapping was checked against an actual A-key press.  Legends are
the stock keycaps; counts always follow the physical switch, even after remaps.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    row: int
    col: int
    legend: str
    x: float
    y: float
    width: float = 1.0
    height: float = 1.0


def _make_layout() -> tuple[Key, ...]:
    keys: list[Key] = []

    def add(row, col, legend, x, y, width=1.0, height=1.0):
        keys.append(Key(row, col, legend, x, y, width, height))

    add(0, 0, "Esc", 0, 0)
    for col in range(1, 13):
        x = (2 if col <= 4 else 6.5 if col <= 8 else 11) + (col - (1 if col <= 4 else 5 if col <= 8 else 9))
        add(0, col, f"F{col}", x, 0)
    for col, legend, x in ((13, "Prt", 16.25), (14, "Scr", 17.25), (15, "Pau", 18.25)):
        add(0, col, legend, x, 0)

    add(1, 0, "`", 0, 1.25)
    for col, legend in enumerate("1234567890-=", 1):
        add(1, col, legend, col, 1.25)
    add(1, 13, "Back", 13, 1.25, 2)
    for row, col, legend, x, y in (
        (1, 14, "Ins", 16.25, 1.25), (1, 15, "Home", 17.25, 1.25),
        (2, 14, "PgUp", 18.25, 1.25), (2, 15, "Del", 16.25, 2.25),
        (3, 14, "End", 17.25, 2.25), (3, 15, "PgDn", 18.25, 2.25),
        (4, 13, "↑", 17.25, 4.25),
        (5, 8, "←", 16.25, 5.25), (5, 9, "↓", 17.25, 5.25),
        (5, 10, "→", 18.25, 5.25),
    ):
        add(row, col, legend, x, y)

    add(2, 0, "Tab", 0, 2.25, 1.5)
    for col, legend in enumerate("QWERTYUIOP", 1):
        add(2, col, legend, col + 0.5, 2.25)
    for col, legend in ((11, "["), (12, "]")):
        add(2, col, legend, col + 0.5, 2.25)
    add(2, 13, "\\", 13.5, 2.25, 1.5)

    add(3, 0, "Caps", 0, 3.25, 1.75)
    for col, legend in enumerate("ASDFGHJKL;'", 1):
        add(3, col, legend, col + 0.75, 3.25)
    add(3, 13, "Enter", 12.75, 3.25, 2.25)

    add(4, 0, "Shift", 0, 4.25, 2.25)
    for col, legend in enumerate("ZXCVBNM,./", 2):
        add(4, col, legend, col + 0.25, 4.25)
    add(4, 12, "Shift", 12.25, 4.25, 2.75)

    for col, legend, x, width in (
        (0, "Ctrl", 0, 1.25), (1, "Win", 1.25, 1.25),
        (2, "Alt", 2.5, 1.25), (3, "Space", 3.75, 6.25),
        (4, "Alt", 10, 1.25), (5, "Win", 11.25, 1.25),
        (6, "Fn", 12.5, 1.25), (7, "Ctrl", 13.75, 1.25),
    ):
        add(5, col, legend, x, 5.25, width)

    for row, col, legend, x, y, width, height in (
        (6, 5, "Num", 20.5, 1.25, 1, 1), (6, 9, "/", 21.5, 1.25, 1, 1),
        (4, 15, "*", 22.5, 1.25, 1, 1), (5, 15, "-", 23.5, 1.25, 1, 1),
        (6, 4, "7", 20.5, 2.25, 1, 1), (6, 8, "8", 21.5, 2.25, 1, 1),
        (4, 14, "9", 22.5, 2.25, 1, 1), (5, 14, "+", 23.5, 2.25, 1, 2),
        (6, 3, "4", 20.5, 3.25, 1, 1), (6, 7, "5", 21.5, 3.25, 1, 1),
        (6, 13, "6", 22.5, 3.25, 1, 1),
        (6, 2, "1", 20.5, 4.25, 1, 1), (6, 6, "2", 21.5, 4.25, 1, 1),
        (6, 12, "3", 22.5, 4.25, 1, 1), (5, 13, "Enter", 23.5, 4.25, 1, 2),
        (5, 11, "0", 20.5, 5.25, 2, 1), (5, 12, ".", 22.5, 5.25, 1, 1),
    ):
        add(row, col, legend, x, y, width, height)
    return tuple(keys)


KEYS = _make_layout()
KEY_BY_MATRIX = {(key.row, key.col): key for key in KEYS}


def heat_color(count: int, peak: int) -> str:
    """A discrete lavender ramp that remains readable under dark key legends."""
    if count <= 0:
        return "#f2f4fc"
    fraction = count / max(1, peak)
    if fraction < 0.08:
        return "#e4dafb"
    if fraction < 0.22:
        return "#d2bdf4"
    if fraction < 0.48:
        return "#ba98eb"
    if fraction < 0.75:
        return "#a67fe2"
    return "#9265d4"
