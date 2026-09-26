"""Pixel-accurate frame editor for DP104 animations."""

from __future__ import annotations

import colorsys
import tkinter as tk

from .effects import COLS, ROWS, MAX_FRAMES
from .image_import import rgb_hex_to_hsv_pixel
from .pixel_theme import (BLUE, FONT, INK, MINT, MUTED, PAPER, PINK,
                          PIXEL_FONT, YELLOW, ask_color, button, label, pixel_entry)

CELL = 22
PALETTE = ("#fff0d9", "#ff8cae", "#ffda73", "#85e7e4", "#8cb8ff", "#b7a6f6",
           "#ff694f", "#76dc91")


def pixel_hex(pixel: bytes) -> str:
    if len(pixel) != 3 or pixel[2] == 0:
        return "#222641"
    red, green, blue = colorsys.hsv_to_rgb(pixel[0] / 255, pixel[1] / 255, pixel[2] / 255)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


class PixelEditor:
    def __init__(self, parent, frames: list[bytes], fps: int, on_apply, on_save, on_upload):
        self.window = tk.Toplevel(parent)
        self.window.title("8×24 像素动画编辑器")
        self.window.configure(bg=PAPER)
        self.window.resizable(True, False)
        self.frames = [bytearray(frame) for frame in frames]
        if not self.frames:
            self.frames = [bytearray(ROWS * COLS * 3)]
        if any(len(frame) != ROWS * COLS * 3 for frame in self.frames):
            raise ValueError("动画帧不是 8×24 HSV 数据")
        self.fps = max(1, min(30, fps))
        self.index = 0
        self.color = PALETTE[0]
        self.tool = "画笔"
        self.onion = tk.BooleanVar(value=True)
        self.playing = False
        self.play_after = None
        self.timeline_buttons = []
        self.highlighted_index = None
        self.undo_stack: list[tuple[list[bytearray], int]] = []
        self.on_apply = on_apply
        self.on_save = on_save
        self.on_upload = on_upload
        self._build()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Control-z>", lambda event: self.undo())
        self.window.transient(parent)
        self.window.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.window.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.window.winfo_height()) // 2)
        self.window.geometry(f"+{x}+{y}")
        self.window.lift(parent)

    def _build(self):
        head = tk.Frame(self.window, bg=PAPER, padx=18, pady=12)
        head.pack(fill="x")
        label(head, "▦  8×24 像素动画编辑器", 15, INK, True).pack(anchor="w")
        label(head, f"点按或拖动改灯珠 · 最多 {MAX_FRAMES} 帧 · 预览和保存都不会立刻写入键盘", 9, MUTED).pack(anchor="w")

        body = tk.Frame(self.window, bg=PAPER, padx=18)
        body.pack(fill="x")
        self.canvas = tk.Canvas(body, width=COLS * CELL, height=ROWS * CELL,
                                bg="#222641", highlightthickness=3,
                                highlightbackground=INK, cursor="crosshair")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._start_stroke)
        self.canvas.bind("<B1-Motion>", self._paint_event)
        self.canvas.bind("<Button-3>", self._erase_event)
        self.canvas.bind("<B3-Motion>", self._erase_event)
        self.canvas.bind("<Left>", lambda event: self.select_frame(max(0, self.index - 1)))
        self.canvas.bind("<Right>", lambda event: self.select_frame(min(len(self.frames) - 1, self.index + 1)))

        side = tk.Frame(body, bg=PAPER, padx=13)
        side.pack(side="left", fill="y")
        label(side, "工具", 10, INK, True).pack(anchor="w")
        row = tk.Frame(side, bg=PAPER)
        row.pack(anchor="w", pady=(3, 9))
        self.tool_buttons = {}
        for name in ("画笔", "橡皮", "取色"):
            control = button(row, name, lambda n=name: self.set_tool(n),
                             tone="pink" if name == self.tool else "plain", pady=4)
            control.pack(side="left", padx=(0, 5))
            self.tool_buttons[name] = control
        label(side, "颜色", 10, INK, True).pack(anchor="w")
        palette = tk.Frame(side, bg=PAPER)
        palette.pack(anchor="w", pady=(3, 4))
        for i, color in enumerate(PALETTE):
            swatch = tk.Button(palette, bg=color, activebackground=color,
                               relief="solid", bd=2, width=3, height=1,
                               command=lambda c=color: self.choose_color(c), cursor="hand2")
            swatch.grid(row=i // 4, column=i % 4, padx=2, pady=2)
        self.color_button = button(side, "自选颜色…", self.custom_color, pady=4)
        self.color_button.pack(anchor="w", pady=(2, 6))
        tk.Checkbutton(side, text="显示上一帧参考", variable=self.onion, command=self.draw,
                       bg=PAPER, fg=INK, activebackground=PAPER,
                       font=(FONT, 9), takefocus=True).pack(anchor="w")
        self.color_hint = label(side, self.color, 9, MUTED)
        self.color_hint.pack(anchor="w", pady=(4, 0))

        timeline = tk.Frame(self.window, bg=PAPER, padx=18, pady=12)
        timeline.pack(fill="x")
        self.frame_label = label(timeline, "", 10, INK, True)
        self.frame_label.pack(anchor="w")
        strip = tk.Frame(timeline, bg=PAPER)
        strip.pack(fill="x", pady=(5, 5))
        button(strip, "◀", lambda: self.strip_canvas.xview_scroll(-4, "units"),
               pady=3).pack(side="left", padx=(0, 4))
        self.strip_canvas = tk.Canvas(strip, height=43, bg=PAPER,
                                      highlightthickness=0)
        self.strip_canvas.pack(side="left", fill="x", expand=True)
        button(strip, "▶", lambda: self.strip_canvas.xview_scroll(4, "units"),
               pady=3).pack(side="left", padx=(4, 0))
        self.strip_inner = tk.Frame(self.strip_canvas, bg=PAPER)
        self.strip_canvas.create_window((0, 0), window=self.strip_inner, anchor="nw")
        self.strip_inner.bind("<Configure>", lambda event: self.strip_canvas.configure(
            scrollregion=self.strip_canvas.bbox("all")))
        self.strip_canvas.bind("<MouseWheel>", lambda event: self.strip_canvas.xview_scroll(
            -1 if event.delta > 0 else 1, "units"))
        self.strip_canvas.configure(xscrollincrement=50)
        controls = tk.Frame(timeline, bg=PAPER)
        controls.pack(fill="x")
        for text, action, tone in (("+ 复制帧", self.duplicate, "mint"),
                                   ("+ 空白帧", self.add_blank, "plain"),
                                   ("删除帧", self.delete, "plain"),
                                   ("左右镜像", self.mirror, "plain"),
                                   ("清空本帧", self.clear, "plain"),
                                   ("撤销", self.undo, "yellow")):
            button(controls, text, action, tone=tone, pady=4).pack(side="left", padx=(0, 6))
        playback = tk.Frame(self.window, bg=PAPER, padx=18)
        playback.pack(fill="x")
        self.play_button = button(playback, "▶ 试播", self.toggle_play, tone="pink", pady=4)
        self.play_button.pack(side="left", padx=(0, 8))
        label(playback, "帧率", 10, INK, True).pack(side="left", padx=(0, 5))
        self.fps_var = tk.StringVar(value=str(self.fps))
        button(playback, "−", lambda: self.fps_var.set(str(max(1, self._fps() - 1))),
               pady=3).pack(side="left", padx=(0, 3))
        fps_entry = pixel_entry(playback, width=4, textvariable=self.fps_var)
        fps_entry.pack(side="left")
        button(playback, "+", lambda: self.fps_var.set(str(min(30, self._fps() + 1))),
               pady=3).pack(side="left", padx=(3, 0))
        label(playback, "fps", 9, MUTED).pack(side="left", padx=(4, 0))
        footer = tk.Frame(self.window, bg=PAPER, padx=18, pady=14)
        footer.pack(fill="x")
        button(footer, "应用到小屋预览", self.apply, tone="mint", pady=6).pack(side="left", padx=(0, 8))
        button(footer, "存为预设", self.save, tone="yellow", pady=6).pack(side="left", padx=(0, 8))
        button(footer, "送上键盘", self.upload, tone="primary", pady=6).pack(side="left")
        button(footer, "关闭", self.close, pady=6).pack(side="right")
        self.refresh()

    def _snapshot(self):
        self.undo_stack.append(([bytearray(frame) for frame in self.frames], self.index))
        self.undo_stack = self.undo_stack[-30:]

    def _cell(self, event):
        x, y = event.x // CELL, event.y // CELL
        return (x, y) if 0 <= x < COLS and 0 <= y < ROWS else None

    def _start_stroke(self, event):
        if self.tool != "取色":
            self._snapshot()
        self._paint_event(event)

    def _erase_event(self, event):
        if event.type == tk.EventType.ButtonPress:
            self._snapshot()
        self._change_pixel(event, erase=True)

    def _paint_event(self, event):
        cell = self._cell(event)
        if cell is None:
            return
        if self.tool == "取色":
            offset = (cell[1] * COLS + cell[0]) * 3
            self.choose_color(pixel_hex(self.frames[self.index][offset:offset + 3]))
            self.set_tool("画笔")
            return
        self._change_pixel(event, erase=self.tool == "橡皮")

    def _change_pixel(self, event, erase=False):
        cell = self._cell(event)
        if cell is None:
            return
        offset = (cell[1] * COLS + cell[0]) * 3
        pixel = b"\0\0\0" if erase else rgb_hex_to_hsv_pixel(self.color)
        if self.frames[self.index][offset:offset + 3] != pixel:
            self.frames[self.index][offset:offset + 3] = pixel
            self.draw()

    def choose_color(self, color):
        self.color = color
        self.color_hint.config(text=color)

    def custom_color(self):
        selected = ask_color(self.window, "挑选灯珠颜色", self.color)
        if selected:
            self.choose_color(selected)

    def set_tool(self, tool):
        self.tool = tool
        for name, control in self.tool_buttons.items():
            control.config(bg=PINK if name == tool else PAPER)

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        current = self.frames[self.index]
        previous = self.frames[self.index - 1] if self.onion.get() and self.index else None
        for y in range(ROWS):
            for x in range(COLS):
                offset = (y * COLS + x) * 3
                pixel = current[offset:offset + 3]
                color = pixel_hex(pixel)
                if pixel[2] == 0 and previous is not None and previous[offset + 2]:
                    color = "#535a78"
                canvas.create_rectangle(x * CELL + 1, y * CELL + 1,
                                        (x + 1) * CELL - 1, (y + 1) * CELL - 1,
                                        fill=color, outline="#303453", width=1)

    def refresh(self):
        self.draw()
        self.frame_label.config(text=f"第 {self.index + 1} / {len(self.frames)} 帧 · 每帧 192 颗灯珠")
        if len(self.timeline_buttons) != len(self.frames):
            for child in self.strip_inner.winfo_children():
                child.destroy()
            self.timeline_buttons = []
            self.highlighted_index = None
            for i in range(len(self.frames)):
                control = button(self.strip_inner, f"{i + 1:02}",
                                 lambda n=i: self.select_frame(n), tone="plain", pady=3)
                control.pack(side="left", padx=(0, 3))
                self.timeline_buttons.append(control)
        if self.highlighted_index != self.index:
            if self.highlighted_index is not None and self.highlighted_index < len(self.timeline_buttons):
                self.timeline_buttons[self.highlighted_index].configure(bg=PAPER)
            self.timeline_buttons[self.index].configure(bg=PINK)
            self.highlighted_index = self.index

    def select_frame(self, index):
        self.index = index
        self.refresh()

    def duplicate(self):
        if len(self.frames) >= MAX_FRAMES:
            self.frame_label.config(text=f"最多 {MAX_FRAMES} 帧；请删除一帧后再复制")
            return
        self._snapshot()
        self.frames.insert(self.index + 1, bytearray(self.frames[self.index]))
        self.index += 1
        self.refresh()

    def add_blank(self):
        if len(self.frames) >= MAX_FRAMES:
            self.frame_label.config(text=f"最多 {MAX_FRAMES} 帧；请删除一帧后再添加")
            return
        self._snapshot()
        self.frames.insert(self.index + 1, bytearray(ROWS * COLS * 3))
        self.index += 1
        self.refresh()

    def delete(self):
        self._snapshot()
        if len(self.frames) == 1:
            self.frames[0] = bytearray(ROWS * COLS * 3)
        else:
            self.frames.pop(self.index)
            self.index = min(self.index, len(self.frames) - 1)
        self.refresh()

    def mirror(self):
        self._snapshot()
        source = bytes(self.frames[self.index])
        for y in range(ROWS):
            for x in range(COLS):
                dest = (y * COLS + x) * 3
                origin = (y * COLS + COLS - x - 1) * 3
                self.frames[self.index][dest:dest + 3] = source[origin:origin + 3]
        self.draw()

    def clear(self):
        self._snapshot()
        self.frames[self.index] = bytearray(ROWS * COLS * 3)
        self.draw()

    def undo(self):
        if self.undo_stack:
            self.frames, self.index = self.undo_stack.pop()
            self.refresh()

    def toggle_play(self):
        self.playing = not self.playing
        self.play_button.config(text="■ 停止" if self.playing else "▶ 试播")
        if self.playing:
            self._tick()
        elif self.play_after is not None:
            self.window.after_cancel(self.play_after)
            self.play_after = None

    def _tick(self):
        if not self.playing or not self.window.winfo_exists():
            return
        self.index = (self.index + 1) % len(self.frames)
        self.refresh()
        self.play_after = self.window.after(round(1000 / self._fps()), self._tick)

    def _fps(self):
        try:
            return max(1, min(30, int(self.fps_var.get())))
        except ValueError:
            return self.fps

    def _result(self):
        return [bytes(frame) for frame in self.frames], self._fps()

    def apply(self):
        self.on_apply(*self._result())

    def save(self):
        self.on_save(*self._result())

    def upload(self):
        self.on_upload(*self._result())

    def close(self):
        self.playing = False
        if self.play_after is not None:
            self.window.after_cancel(self.play_after)
            self.play_after = None
        self.window.destroy()
