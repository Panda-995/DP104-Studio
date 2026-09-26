"""Reusable, code-native pixel UI pieces for the Windows Tk dashboard."""

from __future__ import annotations

import colorsys
import re
import tkinter as tk

from .effects import COLS, ROWS

BG = "#e9ecfb"
PAPER = "#fffefa"
INK = "#303453"
MUTED = "#535a78"
LILAC = "#b7a6f6"
BLUE = "#8bb8ff"
MINT = "#a7e6cf"
PINK = "#ffc3d8"
YELLOW = "#ffe491"
SHADOW = "#a2a6ce"
FONT = "Microsoft YaHei UI"
PIXEL_FONT = "Consolas"


def label(parent, text: str, size=11, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def panel(parent, title: str, column: int, row: int, *, padx=17, pady=15, **grid):
    shadow = tk.Frame(parent, bg=SHADOW)
    shadow.grid(column=column, row=row, sticky="nsew", padx=7, pady=7, **grid)
    border = tk.Frame(shadow, bg=INK, padx=2, pady=2)
    border.pack(fill="both", expand=True, padx=(0, 5), pady=(0, 5))
    inner = tk.Frame(border, bg=PAPER, padx=padx, pady=pady)
    inner.pack(fill="both", expand=True)
    label(inner, title, 11, INK, True).pack(anchor="w")
    return inner


def button(parent, text: str, command, *, tone="plain", width=None, pady=8):
    colors = {"plain": PAPER, "primary": BLUE, "mint": MINT,
              "pink": PINK, "yellow": YELLOW, "dark": INK}
    fg = PAPER if tone == "dark" else INK
    widget = tk.Button(parent, text=text, command=command, bg=colors[tone], fg=fg,
                       activebackground=LILAC if tone != "dark" else "#515b85",
                       activeforeground=INK if tone != "dark" else PAPER,
                       font=(FONT, 10, "bold"), relief="solid", bd=2,
                       highlightthickness=2, highlightbackground=INK,
                       highlightcolor=LILAC, cursor="hand2", padx=11, pady=pady,
                       width=width, takefocus=True)
    return widget


def pixel_entry(parent, **kwargs):
    options = {"font": (PIXEL_FONT, 11), **kwargs}
    options.update(bg="#f4f5ff", fg=INK, insertbackground=INK,
                   selectbackground=LILAC, relief="solid", bd=2,
                   highlightthickness=1, highlightbackground=INK,
                   highlightcolor=BLUE)
    return tk.Entry(parent, **options)


def pixel_scale(parent, **kwargs):
    return PixelSlider(parent, **kwargs)


class PixelSlider(tk.Frame):
    """Chunky segmented slider shared by all numeric controls."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=PAPER)
        self.low = int(kwargs.get("from_", 0))
        self.high = int(kwargs.get("to", 255))
        self.resolution = max(1, int(kwargs.get("resolution", 1)))
        self.length = max(110, int(kwargs.get("length", 150)))
        self.value = self.low
        self.canvas = tk.Canvas(self, width=self.length, height=32, bg=PAPER,
                                highlightthickness=0, cursor="hand2", takefocus=True)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._pointer)
        self.canvas.bind("<B1-Motion>", self._pointer)
        self.canvas.bind("<Left>", lambda event: self.set(self.value - self.resolution))
        self.canvas.bind("<Right>", lambda event: self.set(self.value + self.resolution))
        self.canvas.bind("<Home>", lambda event: self.set(self.low))
        self.canvas.bind("<End>", lambda event: self.set(self.high))
        self.canvas.bind("<FocusIn>", lambda event: self._paint())
        self.canvas.bind("<FocusOut>", lambda event: self._paint())
        self._paint()

    def get(self):
        return self.value

    def set(self, value):
        rounded = self.low + round((int(value) - self.low) / self.resolution) * self.resolution
        self.value = min(self.high, max(self.low, rounded))
        self._paint()

    def _pointer(self, event):
        self.canvas.focus_set()
        end = self.length - 43
        fraction = min(1.0, max(0.0, (event.x - 7) / (end - 7)))
        self.set(self.low + fraction * (self.high - self.low))

    def _paint(self):
        canvas = self.canvas
        canvas.delete("all")
        end = self.length - 43
        canvas.create_rectangle(5, 7, end + 3, 25, fill="#f4f5ff",
                                outline=LILAC if canvas.focus_get() == canvas else INK, width=2)
        fraction = (self.value - self.low) / max(1, self.high - self.low)
        segments = max(6, round((end - 7) / 13))
        for segment in range(segments):
            x1 = 8 + segment * (end - 9) / segments
            x2 = 7 + (segment + 1) * (end - 9) / segments
            color = BLUE if (segment + 0.5) / segments <= fraction else "#dde1f3"
            canvas.create_rectangle(x1, 10, x2, 22, fill=color, outline="")
        thumb = 7 + fraction * (end - 7)
        canvas.create_rectangle(thumb - 4, 5, thumb + 4, 27,
                                fill=PINK, outline=INK, width=2)
        canvas.create_rectangle(self.length - 37, 5, self.length - 1, 27,
                                fill=YELLOW, outline=INK, width=2)
        canvas.create_text(self.length - 19, 16, text=str(self.value),
                           fill=INK, font=(PIXEL_FONT, 9, "bold"))


def pixel_toggle(parent, text, variable):
    return tk.Checkbutton(parent, text=text, variable=variable, indicatoron=False,
                          bg=PAPER, fg=INK, selectcolor=MINT, activebackground=MINT,
                          activeforeground=INK, relief="solid", bd=2,
                          highlightthickness=1, highlightcolor=BLUE,
                          font=(FONT, 10, "bold"), padx=9, pady=5,
                          cursor="hand2", anchor="w")


class PixelDropdown(tk.Frame):
    """Keyboard-accessible menu with the same squared borders as other controls."""

    def __init__(self, parent, values, width=22):
        super().__init__(parent, bg=INK, padx=2, pady=2)
        self.values = list(values)
        self.value = self.values[0] if self.values else ""
        self.width = width
        self.popup = None
        self._outside_binding = None
        self.trigger = tk.Button(self, text=self._caption(), command=self._open,
                                 bg="#f4f5ff", fg=INK, activebackground=MINT,
                                 activeforeground=INK, relief="flat", bd=0,
                                 highlightthickness=0, font=(FONT, 10, "bold"),
                                 anchor="w", padx=8, pady=5, cursor="hand2",
                                 width=width, takefocus=True)
        self.trigger.pack(fill="both", expand=True)
        self.trigger.bind("<Down>", lambda event: self._open())
        self.trigger.bind("<Return>", lambda event: self._open())

    def _caption(self):
        return f"{self.value}  ▾"

    def get(self):
        return self.value

    def set(self, value):
        if value in self.values:
            self.value = value
            self.trigger.configure(text=self._caption())

    def _open(self):
        if self.popup:
            self._close()
            return
        if not self.values or not self.winfo_viewable():
            return
        top = self.winfo_toplevel()
        popup = tk.Toplevel(top)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=INK, padx=2, pady=2)
        self.popup = popup
        listing = tk.Listbox(popup, width=max(self.width, 18),
                             height=min(9, len(self.values)), bg=PAPER, fg=INK,
                             selectbackground=PINK, selectforeground=INK,
                             relief="flat", bd=0,
                             highlightthickness=0, exportselection=False,
                             font=(FONT, 10), activestyle="none")
        listing.pack()
        for item in self.values:
            listing.insert("end", item)
        listing.selection_set(self.values.index(self.value))
        listing.see(self.values.index(self.value))
        listing.bind("<ButtonRelease-1>", lambda event: self._choose(listing))
        listing.bind("<Return>", lambda event: self._choose(listing))
        listing.bind("<Escape>", lambda event: self._close())
        listing.bind("<Tab>", lambda event: self._close())
        popup.bind("<FocusOut>", lambda event: self.after(80, self._close_if_unfocused))
        popup.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        window_bottom = top.winfo_rooty() + top.winfo_height() - 10
        if y + popup.winfo_reqheight() > min(self.winfo_screenheight(), window_bottom):
            y = self.winfo_rooty() - popup.winfo_reqheight()
        popup.geometry(f"+{x}+{max(0, y)}")
        popup.deiconify()
        popup.attributes("-topmost", True)
        popup.lift(top)
        self._outside_binding = top.bind("<ButtonPress-1>", self._outside_click, add="+")
        listing.focus_force()

    def _outside_click(self, event):
        if self.popup and event.widget is not self.trigger:
            self._close()

    def _choose(self, listing):
        selection = listing.curselection()
        if selection:
            self.set(self.values[selection[0]])
        self._close()

    def _close_if_unfocused(self):
        if self.popup and self.focus_get() not in (self.popup,):
            focused = self.focus_get()
            if focused is None or not str(focused).startswith(str(self.popup)):
                self._close()

    def _close(self):
        popup = self.popup
        if not popup:
            return
        self.popup = None
        try:
            popup.destroy()
        except tk.TclError:
            pass
        if self._outside_binding:
            try:
                self.winfo_toplevel().unbind("<ButtonPress-1>", self._outside_binding)
            except tk.TclError:
                pass
            self._outside_binding = None
        self.trigger.focus_set()


def _dialog(parent, title, message):
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    dialog.configure(bg=INK, padx=3, pady=3)
    content = tk.Frame(dialog, bg=PAPER, padx=18, pady=14)
    content.pack(fill="both", expand=True)
    label(content, f"✦  {title}", 12, INK, True).pack(anchor="w", pady=(0, 8))
    label(content, message, 10, INK, wraplength=400,
          justify="left").pack(anchor="w", pady=(0, 10))
    return dialog, content


def _show_dialog(dialog, parent, focus=None):
    dialog.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_reqheight()) // 2
    dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
    dialog.deiconify()
    dialog.lift(parent)
    dialog.grab_set()
    (focus or dialog).focus_force()
    parent.wait_window(dialog)


def ask_confirm(parent, title, message, confirm="确定") -> bool:
    dialog, content = _dialog(parent, title, message)
    result = [False]
    actions = tk.Frame(content, bg=PAPER)
    actions.pack(anchor="e")

    def finish(value):
        result[0] = value
        dialog.destroy()

    button(actions, "取消", lambda: finish(False), tone="plain", pady=5).pack(side="left", padx=(0, 7))
    button(actions, confirm, lambda: finish(True), tone="primary", pady=5).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    dialog.bind("<Escape>", lambda event: finish(False))
    _show_dialog(dialog, parent)
    return result[0]


def ask_text(parent, title, message, initial="") -> str | None:
    dialog, content = _dialog(parent, title, message)
    result = [None]
    value = tk.StringVar(value=initial)
    field = pixel_entry(content, textvariable=value, width=32)
    field.pack(fill="x", pady=(0, 12))
    actions = tk.Frame(content, bg=PAPER)
    actions.pack(anchor="e")

    def finish(accept):
        if accept:
            result[0] = value.get().strip()
        dialog.destroy()

    button(actions, "取消", lambda: finish(False), tone="plain", pady=5).pack(side="left", padx=(0, 7))
    button(actions, "保存", lambda: finish(True), tone="primary", pady=5).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    dialog.bind("<Escape>", lambda event: finish(False))
    field.bind("<Return>", lambda event: finish(True))
    field.select_range(0, "end")
    _show_dialog(dialog, parent, field)
    return result[0]


def ask_color(parent, title, initial="#ffaa55") -> str | None:
    dialog, content = _dialog(parent, title, "选一种颜色，或填写 #RRGGBB 色值。")
    result = [None]
    value = tk.StringVar(value=initial)
    swatches = tk.Frame(content, bg=PAPER)
    swatches.pack(anchor="w", pady=(0, 10))
    colors = ("#ff5964", "#ff9f1c", "#ffe66d", "#06d6a0", "#00c2d1", "#60a5fa",
              "#9b5de5", "#f15bb5", "#ffffff", "#a0aec0", "#475569", "#101426")
    for index, color in enumerate(colors):
        tk.Button(swatches, bg=color, activebackground=color, width=3, height=1,
                  relief="solid", bd=2, cursor="hand2",
                  command=lambda choice=color: value.set(choice)).grid(
                      row=index // 6, column=index % 6, padx=3, pady=3)
    field = pixel_entry(content, textvariable=value, width=16)
    field.pack(anchor="w", pady=(0, 4))
    error = label(content, "", 9, INK)
    error.pack(anchor="w", pady=(0, 8))
    actions = tk.Frame(content, bg=PAPER)
    actions.pack(anchor="e")

    def finish(accept):
        if accept:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value.get().strip()):
                error.config(text="色值格式应为 #RRGGBB")
                return
            result[0] = value.get().strip().lower()
        dialog.destroy()

    button(actions, "取消", lambda: finish(False), tone="plain", pady=5).pack(side="left", padx=(0, 7))
    button(actions, "使用颜色", lambda: finish(True), tone="primary", pady=5).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    dialog.bind("<Escape>", lambda event: finish(False))
    field.bind("<Return>", lambda event: finish(True))
    _show_dialog(dialog, parent, field)
    return result[0]


CAT = (
    "................",
    ".PP..........PP.",
    ".PPP........PPP.",
    ".PWWPPPPPPWWP...",
    ".PWWWWWWWWWWP...",
    ".PWWWWWWWWWWP...",
    ".PWKWWWWWWKWP...",
    ".PWWWWPPWWWWP...",
    ".PWWWWPPWWWWP...",
    ".PWWWWWWWWWWP...",
    "..PWWWWWWWWP....",
    "...PPWWWWPP.....",
    ".....PPPP.......",
)


def draw_cat(canvas: tk.Canvas, scale=5):
    palette = {"P": INK, "W": PAPER, "K": "#675e9d"}
    for y, line in enumerate(CAT):
        for x, char in enumerate(line):
            if char != ".":
                canvas.create_rectangle(x*scale, y*scale, (x+1)*scale, (y+1)*scale,
                                        fill=palette[char], outline="")


def draw_frame(canvas: tk.Canvas, frame: bytes):
    canvas.delete("pixels")
    width = max(canvas.winfo_width(), 480)
    cell = min((width - 34) / COLS, (max(canvas.winfo_height(), 120) - 30) / ROWS, 22)
    x0 = (width - COLS * cell) / 2
    y0 = 17
    for y in range(ROWS):
        for x in range(COLS):
            index = (y * COLS + x) * 3
            hue, saturation, value = frame[index:index+3]
            if value < 5:
                color = "#2c324f"
            else:
                r, g, b = colorsys.hsv_to_rgb(hue/255, saturation/255, value/255)
                color = f"#{round(r*255):02x}{round(g*255):02x}{round(b*255):02x}"
            left = x0 + x*cell + 2
            top = y0 + y*cell + 2
            canvas.create_rectangle(left, top, left+cell-4, top+cell-4,
                                    fill=color, outline="", tags="pixels")
