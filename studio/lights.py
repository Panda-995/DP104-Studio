"""Keyboard lighting controls: bottom light bar (channel 21) and per-key RGB
(channel 22). Both support brightness / effect / speed / hue-sat color via the
same raw-HID value ids as the dot-matrix screen.

Effect names come from the vendor VIA definition (cfg.ticktype.com).
"""

from __future__ import annotations

import colorsys

BAR = 21
KEYS = 22

# (index, 中文名, 官方英文名)
BAR_EFFECTS = (
    (0, "全关", "All Off"), (1, "纯色", "Solid Color"),
    (2, "呼吸 1", "Breathing 1"), (3, "呼吸 2", "Breathing 2"),
    (4, "呼吸 3", "Breathing 3"), (5, "呼吸 4", "Breathing 4"),
    (6, "彩虹氛围 1", "Rainbow Mood 1"), (7, "彩虹氛围 2", "Rainbow Mood 2"),
    (8, "彩虹氛围 3", "Rainbow Mood 3"), (9, "彩虹旋绕 1", "Rainbow Swirl 1"),
    (10, "彩虹旋绕 2", "Rainbow Swirl 2"), (11, "彩虹旋绕 3", "Rainbow Swirl 3"),
    (12, "彩虹旋绕 4", "Rainbow Swirl 4"), (13, "彩虹旋绕 5", "Rainbow Swirl 5"),
    (14, "彩虹旋绕 6", "Rainbow Swirl 6"), (15, "贪吃蛇 1", "Snake 1"),
    (16, "贪吃蛇 2", "Snake 2"), (17, "贪吃蛇 3", "Snake 3"),
    (18, "贪吃蛇 4", "Snake 4"), (19, "贪吃蛇 5", "Snake 5"),
    (20, "贪吃蛇 6", "Snake 6"), (21, "骑士巡游 1", "Knight 1"),
    (22, "骑士巡游 2", "Knight 2"), (23, "骑士巡游 3", "Knight 3"),
    (24, "圣诞", "Christmas"), (25, "渐变 1", "Gradient 1"),
    (26, "渐变 2", "Gradient 2"), (27, "渐变 3", "Gradient 3"),
    (28, "渐变 4", "Gradient 4"), (29, "渐变 5", "Gradient 5"),
    (30, "渐变 6", "Gradient 6"), (31, "渐变 7", "Gradient 7"),
    (32, "渐变 8", "Gradient 8"), (33, "渐变 9", "Gradient 9"),
    (34, "渐变 10", "Gradient 10"), (35, "RGB 测试", "RGB Test"),
    (36, "交替闪烁", "Alternating"), (37, "星闪 1", "Twinkle 1"),
    (38, "星闪 2", "Twinkle 2"), (39, "星闪 3", "Twinkle 3"),
    (40, "星闪 4", "Twinkle 4"), (41, "星闪 5", "Twinkle 5"),
    (42, "星闪 6", "Twinkle 6"),
)

KEY_EFFECTS = (
    (0, "全关", "All Off"), (1, "纯色", "Solid Color"),
    (2, "字符与修饰键", "Alphas Mods"), (3, "渐变·上下", "Gradient Up/Down"),
    (4, "渐变·左右", "Gradient Left/Right"), (5, "呼吸", "Breathing"),
    (6, "波带·饱和", "Band Sat."), (7, "波带·明度", "Band Val."),
    (8, "风车·饱和", "Pinwheel Sat."), (9, "风车·明度", "Pinwheel Val."),
    (10, "螺旋·饱和", "Spiral Sat."), (11, "螺旋·明度", "Spiral Val."),
    (12, "全彩循环", "Cycle All"), (13, "左右循环", "Cycle Left/Right"),
    (14, "上下循环", "Cycle Up/Down"), (15, "彩虹移动箭羽", "Rainbow Moving Chevron"),
    (16, "由内向外", "Cycle Out/In"), (17, "双向由内向外", "Cycle Out/In Dual"),
    (18, "风车彩虹", "Cycle Pinwheel"), (19, "螺旋彩虹", "Cycle Spiral"),
    (20, "双色信标", "Dual Beacon"), (21, "彩虹信标", "Rainbow Beacon"),
    (22, "彩虹风车", "Rainbow Pinwheels"), (23, "雨滴", "Raindrops"),
    (24, "果冻雨滴", "Jellybean Raindrops"), (25, "色相呼吸", "Hue Breathing"),
    (26, "色相钟摆", "Hue Pendulum"), (27, "色相波浪", "Hue Wave"),
    (28, "像素雨", "Pixel Rain"), (29, "像素流动", "Pixel Flow"),
    (30, "像素分形", "Pixel Fractal"), (31, "打字热力图", "Typing Heatmap"),
    (32, "数字雨", "Digital Rain"), (33, "纯色反应·简单", "Solid Reactive Simple"),
    (34, "纯色反应", "Solid Reactive"), (35, "纯色反应·宽", "Solid Reactive Wide"),
    (36, "纯色反应·多宽", "Solid Reactive Multi Wide"),
    (37, "纯色反应·十字", "Solid Reactive Cross"),
    (38, "纯色反应·多十字", "Solid Reactive Multi Cross"),
    (39, "纯色反应·核心", "Solid Reactive Nexus"),
    (40, "纯色反应·多核心", "Solid Reactive Multi Nexus"),
    (41, "泼溅", "Spash"), (42, "多点泼溅", "Multi Splash"),
    (43, "纯色泼溅", "Solid Splash"), (44, "纯色多点泼溅", "Solid Multi Splash"),
)

GROUPS = {"bar": (BAR, BAR_EFFECTS, "底部灯条"), "keys": (KEYS, KEY_EFFECTS, "键位 RGB")}


def effect_options(group: str) -> list[str]:
    _, table, _ = GROUPS[group]
    return [f"{index} · {cn}" for index, cn, _ in table]


def effect_index(group: str, option: str) -> int | None:
    _, table, _ = GROUPS[group]
    for index, _, _ in table:
        if option.startswith(f"{index} ·"):
            return index
    return None


def color_command_args(hex_color: str) -> tuple[int, int] | None:
    """HSV hue/saturation bytes (value is handled by the brightness control)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return None
    try:
        red, green, blue = (int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    if value <= 0.001:
        return (0, 0)
    return (round(hue * 255) & 255, round(saturation * 255))
