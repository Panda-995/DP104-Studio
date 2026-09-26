"""DP104 Studio — a small pixel cottage for a Windows keyboard."""

from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw
import pystray

from studio import ai_designer, image_import, lights, pets, presetlib, scheduler, startup, transitions
from studio.device import DeviceWorker, MODES
from studio.editor import PixelEditor
from studio.effects import COLS, EFFECTS, MAX_FRAMES, ROWS, dim_frames, make_frames
from studio.metrics import TypingMetrics
from studio.keymap import KEYS as PHYSICAL_KEYS, KEY_BY_MATRIX, heat_color
from studio.milestones import Milestones
from studio.pixel_theme import (BG, BLUE, CAT, FONT, INK, LILAC, MINT, MUTED,
                                PAPER, PINK, PIXEL_FONT, YELLOW, PixelDropdown,
                                ask_color, ask_confirm, ask_text, button,
                                draw_cat, draw_frame, label, panel,
                                pixel_entry, pixel_scale, pixel_toggle)
from studio.presets import make_milestone_frames


def acquire_mutex():
    if os.name != "nt":
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\DP104_E560_E104_Controller")
    if not handle or kernel.GetLastError() == 183:
        if handle:
            kernel.CloseHandle(handle)
        raise RuntimeError("已有 DP104 控制程序运行，请先退出旧程序。")
    return handle


class AutoWorker:
    """Background scheduler checks: time-of-day scenes and typing milestones."""

    def __init__(self, device: DeviceWorker, milestones: Milestones, updates):
        self.device = device
        self.milestones = milestones
        self.updates = updates
        self.stop_event = threading.Event()
        self.schedule_path = data_dir() / "schedule.json"
        self.last_rule_key = ""
        self.thread = threading.Thread(target=self.run, name="dp104-auto", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self.stop_event.wait(20)

    def _tick(self):
        config = scheduler.load(self.schedule_path)
        rule = scheduler.active_rule(config, dt.datetime.now())
        key = "" if rule is None else scheduler.rule_line(rule)
        if rule is None:
            self.last_rule_key = ""
        elif key != self.last_rule_key:
            self.last_rule_key = key
            if rule["kind"] == "mode":
                self.device.send("mode", int(rule["value"]))
                self.updates.put(("notice", f"定时场景：{scheduler.describe(rule)} 已生效"))
            else:
                self.updates.put(("notice", "定时亮度规则未执行：当前固件屏幕亮度指令无可见效果"))
        keys = 0
        try:
            keys = self._keys()
        except Exception:
            keys = 0
        if keys > 0:
            fired = self.milestones.sync(keys)
            if fired and not self.device.uploading:
                self.updates.put(("milestone", fired))

    def _keys(self):
        snapshot = self.device.metrics.snapshot()
        return int(snapshot.get("keys_today", 0))


def data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "DP104Studio"


class Studio:
    def __init__(self, mutex):
        self.mutex = mutex
        self.root = tk.Tk()
        self.root.title("DP104 像素小屋")
        self.root.geometry("1190x900")
        self.root.minsize(1060, 840)
        self.root.configure(bg=BG)
        self.start_in_tray = "--tray" in sys.argv
        if self.start_in_tray:
            self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Unmap>", self.on_minimize)
        self.updates = queue.Queue()
        self.metrics = TypingMetrics(data_dir() / "typing.json")
        self.device = DeviceWorker(self.metrics, self.updates)
        self.milestones = Milestones(data_dir() / "milestone.json")
        self.auto = AutoWorker(self.device, self.milestones, self.updates)
        self.ai_settings_path = data_dir() / "ai.json"
        ai = ai_designer.load_settings(self.ai_settings_path)
        self.ai_frames = None
        self.pixel_editor = None
        self.device_state = {"connected": False, "serial": False, "port": None, "serial_error": ""}
        self.lights_loaded = {"keys": False, "bar": False}
        self.mode = None
        self.brightness = None
        self.notice = "小屋已开门，正在寻找 DP104 键盘…"
        self.preview_frames = make_frames("火焰")
        self.upload_brightness = 255
        self.preview_fps = 8
        self.preview_index = 0
        self.preview_name = "火焰"
        self.page = "screen"
        self.preset_tab = "effects"
        self.closing = False
        self.tray = None
        self.tray_ready = threading.Event()
        self.tray_error = ""
        self.tray_hint_shown = False
        self._build()
        self._start_tray()
        if self.start_in_tray:
            self.root.after(200, self._finish_startup_tray)
        self.device.start()
        self.auto.start()
        self.root.after(150, self._refresh_timer)
        self.root.after(round(1000 / self.preview_fps), self.animate_preview)
        if os.environ.get("DP104_STUDIO_SMOKE") == "1":
            self.root.after(7000, self.close)

    # ---------------------------------------------------------------- UI 框架
    def _build(self):
        header = tk.Frame(self.root, bg=INK, height=90, padx=26, pady=7)
        header.pack(fill="x")
        mascot = tk.Canvas(header, width=81, height=65, bg=INK, highlightthickness=0)
        mascot.pack(side="left", padx=(0, 18))
        draw_cat(mascot, 5)
        heading = tk.Frame(header, bg=INK)
        heading.pack(side="left", fill="y")
        tk.Label(heading, text="DP104  像素小屋", bg=INK, fg=PAPER,
                 font=(FONT, 20, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(heading, text="PIXEL COTTAGE  /  键盘的秘密基地", bg=INK, fg=YELLOW,
                 font=(PIXEL_FONT, 10, "bold")).pack(anchor="w")
        self.header_status = tk.Label(header, text="● 寻找键盘", bg=INK, fg=YELLOW,
                                      font=(FONT, 11, "bold"), padx=10, pady=7)
        self.header_status.pack(side="right")

        navigation = tk.Frame(self.root, bg=BG, padx=25, pady=5)
        navigation.pack(fill="x")
        self.nav_buttons = {}
        for page, caption in (("screen", "▦  点阵工坊"), ("home", "⌂  今日总览"),
                              ("setup", "⚙  小屋设置")):
            control = button(navigation, caption, lambda p=page: self.change_page(p),
                             tone="pink" if page == self.page else "plain", width=14, pady=5)
            control.pack(side="left", padx=(0, 8))
            self.nav_buttons[page] = control
        label(navigation, "8 × 24 像素 · 内置模式走 HID，画面上传走串口", 10, MUTED).pack(side="right")

        footer = tk.Frame(self.root, bg=INK, padx=25, pady=5)
        footer.pack(side="bottom", fill="x")
        self.notice_label = tk.Label(footer, text=self.notice, bg=INK, fg=PAPER,
                                     font=(FONT, 10), anchor="w")
        self.notice_label.pack(side="left", fill="x", expand=True)
        button(footer, "收进托盘", self.hide, tone="yellow", pady=4).pack(side="right")
        button(footer, "退出程序", self.close, tone="plain", pady=4).pack(side="right", padx=(0, 8))

        self.content = tk.Frame(self.root, bg=BG, padx=18, pady=3)
        self.content.pack(fill="both", expand=True)
        self.screen_page = tk.Frame(self.content, bg=BG)
        self.home_page = tk.Frame(self.content, bg=BG)
        self.setup_page = tk.Frame(self.content, bg=BG)
        self._build_screen()
        self._build_home()
        self._build_setup()
        self.change_page("screen")

    def _build_screen(self):
        page = self.screen_page
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=2)
        page.grid_rowconfigure(0, weight=0, minsize=292)
        page.grid_rowconfigure(1, weight=1, minsize=296)

        preview_card = panel(page, "▦  小屏幕预览", 0, 0)
        label(preview_card, "选中的画面会在上传前先在这里预览", 10, MUTED).pack(anchor="w", pady=(2, 5))
        self.preview = tk.Canvas(preview_card, height=112, bg="#222641", highlightthickness=3,
                                 highlightbackground=INK)
        self.preview.pack(fill="x", pady=(0, 5))
        self.preview.bind("<Configure>", lambda event: self.draw_preview())
        self.preview_caption = label(preview_card, "火焰 · 点一下就能送上屏", 10, MUTED)
        self.preview_caption.pack(anchor="w", pady=(0, 4))
        label(preview_card, "内置模式由固件自己渲染，即时生效不走串口", 10, INK, True).pack(anchor="w")
        modes = tk.Frame(preview_card, bg=PAPER)
        modes.pack(fill="x", pady=(4, 0))
        self.mode_buttons = {}
        for value, name in ((3, "连接状态"), (1, "打字"), (4, "烟花"),
                            (5, "音乐"), (6, "滚动"), (0, "熄屏")):
            control = button(modes, name, lambda m=value: self.set_mode(m), tone="plain", pady=4)
            control.pack(side="left", padx=(0, 6))
            self.mode_buttons[value] = control

        connection = panel(page, "✦  键盘连接", 1, 0)
        self.keyboard_label = label(connection, "正在寻找 DP104…", 17, INK, True)
        self.keyboard_label.pack(anchor="w", pady=(7, 2))
        self.serial_label = label(connection, "USB 串口：探测中", 11, MUTED)
        self.serial_label.pack(anchor="w")
        self.connection_tip = label(connection, "端口由设备序列号自动配对，不用手填 COM 号。", 9, MUTED,
                                    wraplength=330, justify="left")
        self.connection_tip.pack(anchor="w", pady=(4, 9))
        recovery = tk.Frame(connection, bg=PAPER)
        recovery.pack(anchor="w")
        button(recovery, "重新探测", lambda: self.device.send("probe_serial", None), tone="mint", pady=6).pack(side="left", padx=(0, 7))
        button(recovery, "重启键盘 USB", self.restart_usb, tone="yellow", pady=6).pack(side="left")
        label(connection, "USB 重启需管理员确认，键盘将短暂断开。", 9, MUTED,
              wraplength=330, justify="left").pack(anchor="w", pady=(5, 8))

        presets = panel(page, "★  小屋工坊", 0, 1, columnspan=2, padx=17, pady=8)
        gallery_header = tk.Frame(presets, bg=PAPER)
        gallery_header.pack(fill="x", pady=(0, 3))
        self.gallery_hint = label(gallery_header, "", 10, MUTED)
        self.gallery_hint.pack(side="left")
        self.gallery_tabs = {}
        for tab, title in (("effects", "内置特效"), ("create", "像素创作"),
                           ("mine", "我的预设"),
                           ("ai", "AI 工坊"), ("text", "文本槽"), ("lights", "灯光控制")):
            control = button(gallery_header, title, lambda t=tab: self.change_preset_tab(t),
                             tone="pink" if tab == self.preset_tab else "plain", pady=4)
            control.pack(side="right", padx=(6, 0))
            self.gallery_tabs[tab] = control
        self.gallery = tk.Frame(presets, bg=PAPER)
        self.gallery.pack(fill="both", expand=True)
        self.effect_gallery = self._build_effect_gallery()
        self.create_gallery = self._build_create_gallery()
        self.mine_gallery = self._build_mine_gallery()
        self.ai_gallery = self._build_ai_gallery()
        self.text_gallery = self._build_text_gallery()
        self.lights_gallery = self._build_lights_gallery()
        self.change_preset_tab("effects")
        self.draw_preview()

    def _blank_gallery(self, columns=4):
        grid = tk.Frame(self.gallery, bg=PAPER)
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1)
        return grid

    def _build_effect_gallery(self):
        grid = self._blank_gallery()
        for index, name in enumerate(EFFECTS):
            tone = ("pink", "primary", "yellow", "mint")[index % 4]
            control = button(grid, f"✦  {name}",
                             lambda n=name: self.upload_effect(n), tone=tone, pady=8)
            control.grid(row=index//4, column=index%4, sticky="ew", padx=5, pady=3)
        return grid

    def _build_create_gallery(self):
        wrap = tk.Frame(self.gallery, bg=PAPER)
        first = tk.Frame(wrap, bg=PAPER)
        first.pack(fill="x", pady=(1, 7))
        button(first, "▦ 编辑当前动画", self.open_pixel_editor, tone="pink", pady=5).pack(side="left", padx=(0, 7))
        button(first, "导入动图 / .tabml…", self.import_animation, tone="primary", pady=5).pack(side="left", padx=(0, 7))
        button(first, "导入图片序列…", self.import_sequence, tone="mint", pady=5).pack(side="left", padx=(0, 12))
        label(first, "图片适应", 9, MUTED).pack(side="left", padx=(0, 5))
        self.import_fit = PixelDropdown(first, ["完整显示", "裁切填满", "拉伸填满"], width=10)
        self.import_fit.pack(side="left")
        label(wrap, "像素宠物与小剧场 · 点击先预览；可继续编辑、存为预设或送上键盘", 9, MUTED).pack(anchor="w")
        pet_grid = tk.Frame(wrap, bg=PAPER)
        pet_grid.pack(fill="x", pady=(3, 5))
        for col in range(3):
            pet_grid.grid_columnconfigure(col, weight=1)
        for index, (name, description, _, _) in enumerate(pets.SCENES):
            tone = ("pink", "yellow", "mint", "primary")[index % 4]
            button(pet_grid, f"✦ {name} · {description}",
                   lambda n=name: self.preview_pet(n), tone=tone, pady=5).grid(
                       row=index // 3, column=index % 3, sticky="ew", padx=4, pady=3)
        actions = tk.Frame(wrap, bg=PAPER)
        actions.pack(anchor="w")
        button(actions, "存当前预览", self.save_preview_as_preset, tone="mint", pady=4).pack(side="left", padx=(0, 7))
        button(actions, "送上键盘", self.upload_current_preview, tone="primary", pady=4).pack(side="left")
        self.create_status = label(wrap, f"支持 GIF、APNG、WebP 和官方 .tabml；最多保留 {MAX_FRAMES} 帧。", 9, MUTED)
        self.create_status.pack(anchor="w", pady=(3, 0))
        return wrap

    def _build_mine_gallery(self):
        wrap = tk.Frame(self.gallery, bg=PAPER)
        bar = tk.Frame(wrap, bg=PAPER)
        bar.pack(fill="x", pady=(0, 4))
        button(bar, "导入预设…", self.import_preset, tone="plain", pady=4).pack(side="left", padx=(0, 6))
        button(bar, "存当前预览", self.save_preview_as_preset, tone="mint", pady=4).pack(side="left")
        self.mine_area = tk.Frame(wrap, bg=PAPER)
        self.mine_area.pack(fill="both", expand=True)
        self.mine_hint = label(wrap, "", 9, MUTED)
        self.mine_hint.pack(anchor="w", pady=(3, 0))
        return wrap

    def _build_ai_gallery(self):
        wrap = tk.Frame(self.gallery, bg=PAPER)
        label(wrap, "实体屏幕只有 8×24 颗灯珠。写清主体、动作和方向；移动角色会画多种姿态，再展开成 64 帧循环动画。",
              9, MUTED, wraplength=940, justify="left").pack(anchor="w", pady=(0, 4))
        self.ai_prompt = tk.Text(wrap, height=2, font=(FONT, 11), bg="#f4f5ff",
                                 fg=INK, insertbackground=INK, selectbackground=LILAC,
                                 relief="solid", bd=2, highlightthickness=1,
                                 highlightcolor=BLUE, wrap="word")
        self.ai_prompt.pack(fill="x")
        self.ai_prompt.insert("1.0", "例：粉色爱心脉冲呼吸，节奏轻快")
        actions = tk.Frame(wrap, bg=PAPER)
        actions.pack(anchor="w", pady=(6, 2))
        self.ai_generate_button = button(actions, "✨ 让 AI 画", self.generate_ai, tone="pink", pady=6)
        self.ai_generate_button.pack(side="left", padx=(0, 7))
        button(actions, "存为预设", self.save_ai_preset, tone="mint", pady=6).pack(side="left", padx=(0, 7))
        button(actions, "送上键盘", self.upload_ai, tone="primary", pady=6).pack(side="left", padx=(0, 10))
        label(actions, "过渡", 9, MUTED).pack(side="left", padx=(0, 4))
        self.ai_tween = PixelDropdown(actions, ["原始帧", "柔滑 2×", "柔滑 3×"], width=9)
        self.ai_tween.pack(side="left")
        self.ai_save = None
        self.ai_status = label(wrap, "可写「心形连续跳动」或「星星从右向左滚动」。接口与密钥在「小屋设置」配置。", 9, MUTED,
                               wraplength=900, justify="left")
        self.ai_status.pack(anchor="w", pady=(2, 0))
        return wrap

    def _build_text_gallery(self):
        wrap = tk.Frame(self.gallery, bg=PAPER)
        slot_row = tk.Frame(wrap, bg=PAPER)
        slot_row.pack(anchor="w", pady=(0, 5))
        label(slot_row, "槽位", 10, INK, True).pack(side="left", padx=(0, 5))
        self.slot_index = 0
        self.slot_buttons = []
        for slot in range(5):
            control = button(slot_row, f"槽 {slot + 1}", lambda s=slot: self.select_slot(s),
                             tone="pink" if slot == 0 else "plain", width=7, pady=4)
            control.pack(side="left", padx=(0, 5))
            self.slot_buttons.append(control)
        self.text_entry = pixel_entry(wrap, font=(PIXEL_FONT, 12), width=34)
        self.text_entry.pack(fill="x", pady=(0, 5))
        rows = tk.Frame(wrap, bg=PAPER)
        rows.pack(anchor="w")
        button(rows, "读入文本槽", self.read_slot_text, tone="plain", pady=5).pack(side="left", padx=(0, 6))
        button(rows, "写入此槽", self.write_slot_text, tone="primary", pady=5).pack(side="left", padx=(0, 6))
        button(rows, "备份 5 槽…", self.export_slots, tone="mint", pady=5).pack(side="left", padx=(0, 6))
        button(rows, "恢复备份…", self.restore_slots, tone="yellow", pady=5).pack(side="left")
        label(wrap, "固件滚动字体不显示小写字母；手动写入会自动转成大写。仅支持 ASCII、最多 30 字符。",
              9, MUTED, wraplength=940, justify="left").pack(anchor="w", pady=(5, 0))
        self.slot_hint = label(wrap, "", 9, INK, True)
        self.slot_hint.pack(anchor="w", pady=(2, 0))
        return wrap

    def _build_lights_gallery(self):
        wrap = tk.Frame(self.gallery, bg=PAPER)
        top = tk.Frame(wrap, bg=PAPER)
        top.pack(fill="x", pady=(0, 4))
        label(top, "DIY 上传亮度", 10, INK, True).pack(side="left")
        self.screen_brightness = pixel_scale(top, from_=0, to=255, length=176)
        self.screen_brightness.set(255)
        self.screen_brightness.pack(side="left", padx=(6, 4))
        button(top, "按此亮度重传", lambda: self.apply_screen_brightness(self.screen_brightness.get()),
               tone="primary", pady=4).pack(side="left", padx=(0, 14))
        button(top, "读取灯光当前值", self.read_lights, tone="plain", pady=4).pack(side="left")

        groups = tk.Frame(wrap, bg=PAPER)
        groups.pack(fill="x")
        self.light_widgets = {}
        for column, group in enumerate(("keys", "bar")):
            title = lights.GROUPS[group][2]
            box = tk.LabelFrame(groups, text=f"  {title}  ", bg=PAPER, fg=INK,
                                font=(FONT, 10, "bold"), bd=2, relief="solid")
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 12) if group == "keys" else 0)
            widgets = {}
            row = tk.Frame(box, bg=PAPER)
            row.pack(anchor="w", pady=(2, 0))
            label(row, "亮度", 9, MUTED).pack(side="left")
            widgets_brightness = pixel_scale(row, from_=0, to=255, length=142)
            widgets_brightness.set(200)
            widgets_brightness.pack(side="left", padx=(4, 10))
            widgets["brightness"] = widgets_brightness
            label(row, "速度", 9, MUTED).pack(side="left")
            speed = pixel_scale(row, from_=0, to=255, length=110)
            speed.set(80)
            speed.pack(side="left")
            widgets["speed"] = speed
            second = tk.Frame(box, bg=PAPER)
            second.pack(anchor="w", pady=(2, 3))
            label(second, "效果", 9, MUTED).pack(side="left")
            effect = ttk_combobox(second, lights.effect_options(group), width=22)
            effect.pack(side="left", padx=(4, 10))
            widgets["effect"] = effect
            pick = button(second, "🎨 颜色", lambda g=group: self.pick_light_color(g),
                          tone="plain", pady=3)
            pick.pack(side="left", padx=(0, 10))
            widgets["color"] = "#ffaa55"
            button(second, "应用整组", lambda g=group: self.apply_lights(g),
                   tone="primary", pady=4).pack(side="left")
            self.light_widgets[group] = widgets
        groups.grid_columnconfigure(0, weight=1)
        groups.grid_columnconfigure(1, weight=1)
        label(wrap, "DIY 屏幕亮度通过调整动画像素并重传生效；固件亮度命令实测无可见效果。键位与灯条设置会保存到键盘。",
              9, MUTED, wraplength=940, justify="left").pack(anchor="w", pady=(4, 0))
        return wrap

    def _build_home(self):
        page = self.home_page
        page.grid_columnconfigure(0, weight=2)
        page.grid_columnconfigure(1, weight=3)
        page.grid_rowconfigure(0, weight=0, minsize=210)
        page.grid_rowconfigure(1, weight=1, minsize=350)

        typing = panel(page, "⌨  今日打字", 0, 0)
        self.keys_label = label(typing, "0", 36, INK, True)
        self.keys_label.pack(anchor="w", pady=(8, 0))
        label(typing, "次按键估算 · 只计本键盘，不保存输入内容", 9, MUTED).pack(anchor="w")
        self.speed_label = label(typing, "0 键/分  ·  约 0 WPM", 14, INK, True)
        self.speed_label.pack(anchor="w", pady=(14, 0))
        self.milestone_label = label(typing, "里程碑：读取中", 10, MUTED)
        self.milestone_label.pack(anchor="w", pady=(14, 0))

        keymap = panel(page, "⌨  DP104 今日键位图", 0, 1, columnspan=2)
        self.keymap = tk.Canvas(keymap, height=292, bg="#e8eaf9", highlightthickness=2,
                                highlightbackground=INK)
        self.keymap.pack(fill="both", expand=True, pady=(5, 5))
        self.keymap.bind("<Configure>", lambda event: self.draw_keymap())
        self.keymap.bind("<Button-1>", self.select_keymap_key)
        self.keymap_hitboxes = []
        self.selected_matrix_key = None
        self.keymap_caption = label(keymap, "颜色越深，今天按得越多 · 点击键帽查看次数 · 只统计 DP104",
                                    9, MUTED)
        self.keymap_caption.pack(anchor="w", pady=(0, 3))

        status = panel(page, "▣  键盘连接", 1, 0)
        self.home_device_label = label(status, "正在寻找 DP104…", 13, INK, True)
        self.home_device_label.pack(anchor="w", pady=(20, 8))
        label(status, "内置模式走 HID；DIY 亮度会随动画重传。AI/特效画面经 USB 串口发送，动画传入后由键盘自己循环播放。",
              9, MUTED, wraplength=380, justify="left").pack(anchor="w")
        button(status, "去点阵工坊 →", lambda: self.change_page("screen"),
               tone="pink", pady=6).pack(anchor="w", pady=(22, 0))

    def _build_setup(self):
        page = self.setup_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=0, minsize=125)
        page.grid_rowconfigure(2, weight=0, minsize=105)

        ai = panel(page, "✨  AI 接入（OpenAI 兼容）", 0, 0)
        label(ai, "支持 Chat Completions 兼容接口；可填基础地址或完整 /chat/completions 地址。", 9, MUTED).pack(anchor="w", pady=(2, 6))
        fields = tk.Frame(ai, bg=PAPER)
        fields.pack(fill="x")
        self.ai_url = tk.StringVar()
        self.ai_key = tk.StringVar()
        self.ai_model = tk.StringVar()
        saved_ai = ai_designer.load_settings(self.ai_settings_path)
        self.ai_url.set(saved_ai["base_url"])
        self.ai_key.set(saved_ai["api_key"])
        self.ai_model.set(saved_ai["model"])
        for row, (title, variable, masked) in enumerate((
                ("接口地址", self.ai_url, False),
                ("API Key", self.ai_key, True),
                ("模型名称", self.ai_model, False))):
            label(fields, title, 10, INK, True).grid(row=row, column=0, sticky="w", pady=3)
            entry = pixel_entry(fields, textvariable=variable, width=44)
            if masked:
                entry.configure(show="*")
            entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=3)
        fields.grid_columnconfigure(1, weight=1)
        actions = tk.Frame(ai, bg=PAPER)
        actions.pack(anchor="w", pady=(8, 0))
        button(actions, "保存配置", self.save_ai_settings, tone="primary", pady=6).pack(side="left", padx=(0, 7))
        button(actions, "测试连接", self.test_ai_settings, tone="mint", pady=6).pack(side="left")
        self.ai_test_label = label(ai, "密钥只存在本机配置目录，不会写进程序或日志。", 9, MUTED,
                                   wraplength=700, justify="left")
        self.ai_test_label.pack(anchor="w", pady=(6, 0))

        scenes = panel(page, "⏱  定时场景", 1, 0)
        self.scene_enabled = tk.BooleanVar(value=False)
        pixel_toggle(scenes, "启用定时场景（仅支持内置屏幕模式）",
                     self.scene_enabled).pack(anchor="w")
        self.rule_list = tk.Listbox(scenes, height=4, font=(PIXEL_FONT, 11), bg="#f4f5ff",
                                    fg=INK, selectbackground=LILAC, selectforeground=INK,
                                    relief="solid", bd=2, highlightthickness=0)
        self.rule_list.pack(fill="x", pady=(4, 4))
        rule_actions = tk.Frame(scenes, bg=PAPER)
        rule_actions.pack(anchor="w")
        button(rule_actions, "添加规则", self.add_rule, tone="pink", pady=4).pack(side="left", padx=(0, 6))
        button(rule_actions, "删除选中", self.delete_rule, tone="plain", pady=4).pack(side="left", padx=(0, 6))
        button(rule_actions, "保存场景", self.save_scenes, tone="primary", pady=4).pack(side="left")
        label(scenes, "时段可跨零点（如 22:00 - 07:00）；多个规则命中时以列表最后的为准。",
              9, MUTED, wraplength=460, justify="left").pack(anchor="w", pady=(4, 0))

        milestone = panel(page, "⌨  打字里程碑", 0, 1, columnspan=2, pady=7)
        row = tk.Frame(milestone, bg=PAPER)
        row.pack(anchor="w", pady=(2, 0))
        self.milestone_enabled = tk.BooleanVar(value=self.milestones.enabled)
        milestone_toggle = pixel_toggle(row, "每满一档自动推一帧庆祝画面（不打扰 DIY 播放）",
                                        self.milestone_enabled)
        milestone_toggle.configure(pady=2)
        milestone_toggle.pack(side="left")
        row2 = tk.Frame(milestone, bg=PAPER)
        row2.pack(anchor="w")
        label(row2, "档位（每多少键）", 10, INK, True).pack(side="left")
        self.milestone_step = pixel_scale(row2, from_=1000, to=20000,
                                          resolution=500, length=200)
        self.milestone_step_value = self.milestones.step
        self.milestone_step.set(self.milestones.step)
        self.milestone_step.pack(side="left", padx=(8, 10))
        button(row2, "保存里程碑设置", self.save_milestone, tone="primary", pady=4).pack(side="left")
        label(milestone, "庆祝帧只在串口空闲且屏幕不在 DIY 画面时推送，一天最多几次。",
              9, MUTED, wraplength=940, justify="left").pack(anchor="w", pady=(1, 0))

        data = panel(page, "▣  数据", 0, 2, columnspan=2, pady=7)
        data_row = tk.Frame(data, bg=PAPER)
        data_row.pack(fill="x", pady=(2, 0))
        label(data_row, "打字统计、AI 配置与预设只保存在本机。",
              9, MUTED).pack(side="left")
        button(data_row, "打开数据目录", self.open_data_dir, tone="plain", pady=3).pack(side="right")
        self.startup_enabled = tk.BooleanVar(value=startup.is_enabled())
        startup_toggle = pixel_toggle(data_row, "开机启动 · 进入托盘", self.startup_enabled)
        startup_toggle.configure(command=self.toggle_startup, pady=2)
        startup_toggle.pack(side="right", padx=(8, 14))
        self._load_scenes()

    # ---------------------------------------------------------------- 页面切换
    def change_page(self, page):
        self._close_dropdowns()
        self.page = page
        for child in (self.screen_page, self.home_page, self.setup_page):
            child.pack_forget()
        {"screen": self.screen_page, "home": self.home_page,
         "setup": self.setup_page}[page].pack(fill="both", expand=True)
        for key, control in self.nav_buttons.items():
            control.configure(bg=PINK if key == page else PAPER)
        self.nav_buttons[page].focus_set()

    def change_preset_tab(self, tab):
        self._close_dropdowns()
        self.preset_tab = tab
        for child in self.gallery.winfo_children():
            child.pack_forget()
        {"effects": self.effect_gallery, "create": self.create_gallery,
         "mine": self.mine_gallery,
         "ai": self.ai_gallery, "text": self.text_gallery,
         "lights": self.lights_gallery}[tab].pack(fill="both", expand=True)
        for key, control in self.gallery_tabs.items():
            control.configure(bg=PINK if key == tab else PAPER)
        hints = {"effects": "点击任意特效即上传；键盘收到后自行循环播放",
                 "create": "编辑、导入和宠物动画都先在上方预览，再决定是否上传",
                 "mine": "右键预设可编辑 / 重命名 / 删除 / 导出",
                 "ai": "用一句话描述想要的画面，AI 生成后可存为预设",
                 "text": "读写 5 个文本槽 · 小写输入会转为大写再上屏",
                 "lights": "键盘灯条与逐键 RGB 控制 · 写入并保存后生效"}
        self.gallery_hint.config(text=hints.get(tab, ""))
        self.gallery_tabs[tab].focus_set()

    def _close_dropdowns(self):
        for widgets in getattr(self, "light_widgets", {}).values():
            widgets["effect"]._close()
        for control in (getattr(self, "import_fit", None), getattr(self, "ai_tween", None)):
            if control is not None:
                control._close()

    # ---------------------------------------------------------------- 内置操作
    def set_mode(self, value):
        self.device.send("mode", value)
        self.notice = f"正在切换到「{MODES[value]}」模式…"

    def restart_usb(self):
        if not self.device_state.get("connected"):
            self.notice = "请先连接 DP104 键盘"
            return
        self.device.send("restart_usb", None)
        self.notice = "等待 Windows 管理员确认以重启键盘 USB 设备…"

    def upload_effect(self, name):
        if not self.confirm_upload():
            return
        self.preview_frames = make_frames(name)
        self.preview_fps = 8
        self.preview_name = name
        self.preview_index = 0
        self.preview_caption.config(text=f"{name} · 固件收到后自行循环")
        self.draw_preview()
        self._send_upload()

    def set_preview_animation(self, name: str, frames: list[bytes], fps: int):
        self.preview_frames = frames
        self.preview_fps = fps
        self.preview_name = name
        self.preview_index = 0
        self.preview_caption.config(text=f"{name} · {len(frames)} 帧 @ {fps} fps · 循环播放")
        self.draw_preview()

    def upload_current_preview(self, parent=None):
        if self.confirm_upload(parent):
            self._send_upload()

    def preview_pet(self, name: str):
        frames, fps = pets.make_scene(name)
        self.set_preview_animation(name, frames, fps)
        self.create_status.config(text=f"已选「{name}」；{len(frames)} 帧循环，可编辑或上传。")

    def open_pixel_editor(self):
        if self.pixel_editor and self.pixel_editor.window.winfo_exists():
            self.pixel_editor.window.lift(self.root)
            return
        self.pixel_editor = PixelEditor(
            self.root, self.preview_frames, self.preview_fps,
            self.apply_editor_result, self.save_editor_result, self.upload_editor_result)

    def apply_editor_result(self, frames: list[bytes], fps: int):
        name = self.preview_name if self.preview_name.endswith("· 已编辑") else f"{self.preview_name} · 已编辑"
        self.set_preview_animation(name, frames, fps)
        self.notice = "像素编辑已应用到预览；可继续保存或送上键盘"

    def save_editor_result(self, frames: list[bytes], fps: int):
        parent = self.pixel_editor.window if self.pixel_editor else self.root
        name = ask_text(parent, "保存像素动画", "预设名称：", initial=self.preview_name)
        if not name:
            return
        try:
            presetlib.save(name, frames, fps, "editor")
            self.set_preview_animation(name, frames, fps)
            self.refresh_mine_gallery()
            self.notice = f"像素动画已保存为「{name}」"
        except (OSError, ValueError) as exc:
            self.notice = f"保存失败：{exc}"

    def upload_editor_result(self, frames: list[bytes], fps: int):
        self.apply_editor_result(frames, fps)
        self.upload_current_preview(self.pixel_editor.window)

    def _start_image_import(self, paths: list[Path]):
        if not paths:
            return
        mode = self.import_fit.get()
        name = paths[0].stem if len(paths) == 1 else f"图片序列 · {len(paths)} 张"
        self.create_status.config(text=f"正在转换「{name}」…")

        def task():
            try:
                frames, fps = (image_import.import_tabml(paths[0])
                               if len(paths) == 1 and paths[0].suffix.lower() == ".tabml"
                               else image_import.import_images(paths, mode))
                self.updates.put(("image_result", (name, frames, fps)))
            except (OSError, ValueError) as exc:
                self.updates.put(("image_error", str(exc)))
        threading.Thread(target=task, name="dp104-image-import", daemon=True).start()

    def import_animation(self):
        source = filedialog.askopenfilename(parent=self.root, title="选择 GIF、官方动画或图片",
                                            filetypes=[("动图、官方动画与图片", "*.gif *.tabml *.apng *.png *.webp *.jpg *.jpeg *.bmp"),
                                                       ("所有文件", "*.*")])
        if source:
            self._start_image_import([Path(source)])

    def import_sequence(self):
        sources = filedialog.askopenfilenames(parent=self.root, title="选择连续图片（按文件名排序）",
                                              filetypes=[("图片", "*.png *.webp *.jpg *.jpeg *.bmp *.gif"),
                                                         ("所有文件", "*.*")])
        if sources:
            self._start_image_import([Path(source) for source in sources])

    def _send_upload(self):
        if not self.device_state.get("connected"):
            self.notice = "尚未连接键盘；画面已预览，连接后再点击上屏"
            return
        self.device.send("animation", (dim_frames(self.preview_frames, self.upload_brightness), self.preview_fps))
        self.notice = "正在检查串口并发送画面…"

    def confirm_upload(self, parent=None):
        if not self.device_state.get("connected"):
            return True
        ready = ask_confirm(
            parent or self.root,
            "上传前先切换屏幕",
            "请先按点阵屏旁的实体模式键，让键盘显示任一内置预设画面。\n\n"
            "确认后开始上传；完成后再按实体模式键切到 DIY／自定义画面查看。\n"
            "在 DIY 画面播放时直接上传，键盘串口可能卡住。",
            confirm="开始上传",
        )
        if not ready:
            self.notice = "上传已取消；先将键盘屏幕切到预设画面"
        return ready

    def draw_preview(self):
        if hasattr(self, "preview"):
            frame = self.preview_frames[self.preview_index]
            draw_frame(self.preview, dim_frames([frame], self.upload_brightness)[0])

    def animate_preview(self):
        if self.closing:
            return
        if self.page == "screen" and self.root.state() == "normal" and len(self.preview_frames) > 1:
            self.preview_index = (self.preview_index + 1) % len(self.preview_frames)
            self.draw_preview()
        self.root.after(round(1000 / max(1, min(30, self.preview_fps))), self.animate_preview)

    # ---------------------------------------------------------------- 我的预设
    def refresh_mine_gallery(self):
        for child in self.mine_area.winfo_children():
            child.destroy()
        names = presetlib.names()
        for index, name in enumerate(names):
            tone = ("pink", "primary", "yellow", "mint")[index % 4]
            try:
                frames, fps = presetlib.load(name)
            except (OSError, ValueError, KeyError):
                continue
            source = "AI" if self._preset_source(name) == "ai" else "预设"
            control = button(self.mine_area, f"✦  {name}\n{source} · {fps} 帧/秒",
                             lambda n=name: self.upload_saved_preset(n),
                             tone=tone, pady=6)
            control.grid(row=index//4, column=index%4, sticky="ew", padx=5, pady=3)
            control.bind("<Button-3>", lambda event, n=name: self.preset_menu(event, n))
        if not names:
            label(self.mine_area, "还没有预设。用「AI 工坊」生成，或把当前预览存进来；支持导入分享的 .json。",
                  10, MUTED, wraplength=900, justify="left").grid(row=0, column=0, columnspan=4, sticky="w")

    def _preset_source(self, name: str) -> str:
        try:
            data = json.loads((presetlib.library_dir() / f"{presetlib.SAFE_NAME.sub('_', name)}.json")
                              .read_text(encoding="utf-8"))
            return str(data.get("source", ""))
        except (OSError, ValueError):
            return ""

    def upload_saved_preset(self, name: str):
        if not self.confirm_upload():
            return
        try:
            frames, fps = presetlib.load(name)
        except (OSError, ValueError, KeyError) as exc:
            self.notice = f"读取预设失败：{exc}"
            return
        self.preview_frames = frames
        self.preview_fps = fps
        self.preview_name = name
        self.preview_index = 0
        self.preview_caption.config(text=f"{name} · {fps} 帧每秒循环")
        self.draw_preview()
        self._send_upload()

    def preset_menu(self, event, name: str):
        menu = tk.Menu(self.root, tearoff=0, bg=PAPER, fg=INK,
                       activebackground=PINK, activeforeground=INK,
                       font=(FONT, 10), relief="solid", bd=2)
        menu.add_command(label=f"上屏「{name}」", command=lambda: self.upload_saved_preset(name))
        menu.add_command(label="用像素编辑器打开", command=lambda: self.edit_saved_preset(name))
        menu.add_command(label="重命名…", command=lambda: self.rename_preset(name))
        menu.add_command(label="导出…", command=lambda: self.export_preset(name))
        menu.add_command(label="删除", command=lambda: self.delete_preset(name))
        menu.tk_popup(event.x_root, event.y_root)

    def rename_preset(self, name: str):
        new = ask_text(self.root, "重命名预设", "新的预设名：", initial=name)
        if not new or new == name:
            return
        try:
            presetlib.rename(name, new)
            self.notice = f"预设已改名为「{new}」"
        except (OSError, ValueError) as exc:
            self.notice = f"重命名失败：{exc}"
        self.refresh_mine_gallery()

    def edit_saved_preset(self, name: str):
        if self.pixel_editor and self.pixel_editor.window.winfo_exists():
            self.pixel_editor.window.lift(self.root)
            self.notice = "请先关闭当前像素编辑器，再打开另一份预设"
            return
        try:
            frames, fps = presetlib.load(name)
            self.set_preview_animation(name, frames, fps)
            self.open_pixel_editor()
        except (OSError, ValueError, KeyError) as exc:
            self.notice = f"打开预设失败：{exc}"

    def delete_preset(self, name: str):
        if not ask_confirm(self.root, "删除预设", f"确定删除「{name}」？此操作不可恢复。", confirm="删除"):
            return
        presetlib.delete(name)
        self.notice = f"预设「{name}」已删除"
        self.refresh_mine_gallery()

    def export_preset(self, name: str):
        target = filedialog.asksaveasfilename(title="导出预设", defaultextension=".json",
                                              initialfile=f"{name}.json",
                                              filetypes=[("DP104 预设", "*.json")])
        if not target:
            return
        try:
            presetlib.export_to(name, Path(target))
            self.notice = f"已导出到 {target}"
        except OSError as exc:
            self.notice = f"导出失败：{exc}"

    def import_preset(self):
        source = filedialog.askopenfilename(title="选择预设文件",
                                            filetypes=[("DP104 预设", "*.json")])
        if not source:
            return
        try:
            name = presetlib.import_from(Path(source))
            self.notice = f"已导入预设「{name}」"
        except (OSError, ValueError, KeyError) as exc:
            self.notice = f"导入失败：{exc}"
        self.refresh_mine_gallery()

    def save_preview_as_preset(self):
        name = ask_text(self.root, "保存预设", "预设名称：", initial=self.preview_name)
        if not name:
            return
        try:
            presetlib.save(name, self.preview_frames, self.preview_fps, "manual")
            self.notice = f"当前预览已存为预设「{name}」"
        except (OSError, ValueError) as exc:
            self.notice = f"保存失败：{exc}"
        self.refresh_mine_gallery()

    # ---------------------------------------------------------------- AI 工坊
    def generate_ai(self):
        if getattr(self, "ai_generating", False):
            return
        prompt = self.ai_prompt.get("1.0", "end").strip()
        if not prompt or prompt.startswith("例："):
            self.notice = "先写一句描述，比如「蓝色星空缓缓流动」"
            return
        settings = {"base_url": ai_designer.clean_base_url(self.ai_url.get()),
                    "api_key": self.ai_key.get().strip(),
                    "model": self.ai_model.get().strip() or ai_designer.DEFAULT_MODEL}
        if not settings["base_url"]:
            self.notice = "接口地址为空；先到「小屋设置」填写"
            return
        self.ai_generating = True
        self.ai_generate_button.config(state="disabled")
        self.ai_status.config(text=f"{settings['model']} 正在绘制…")

        def task():
            try:
                fps, frames = ai_designer.generate(settings, prompt)
                self.updates.put(("ai_result", (fps, frames)))
            except Exception as exc:
                self.updates.put(("ai_error", str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def apply_ai_result(self, fps, frames):
        self.ai_generating = False
        self.ai_generate_button.config(state="normal")
        requested_factor = {"原始帧": 1, "柔滑 2×": 2, "柔滑 3×": 3}[self.ai_tween.get()]
        factor = min(requested_factor, MAX_FRAMES // len(frames), 30 // fps)
        frames, fps = transitions.tween_frames(frames, fps, factor)
        self.ai_frames = (fps, frames)
        self.preview_frames = frames
        self.preview_fps = fps
        self.preview_name = "AI 作品"
        self.preview_index = 0
        self.preview_caption.config(text=f"AI 作品 · {fps} fps · 共 {len(frames)} 帧")
        self.draw_preview()
        limit_note = "；已按帧数或帧率上限减少补帧" if factor < requested_factor else ""
        self.ai_status.config(text=f"完成！{len(frames)} 帧 @ {fps} fps{limit_note}。过渡补帧不增加灯珠分辨率。", fg=INK)

    def apply_ai_error(self, message: str):
        self.ai_generating = False
        self.ai_generate_button.config(state="normal")
        self.ai_status.config(text=f"生成失败：{message}", fg=INK)
        self.notice = f"AI 生成失败：{message}"

    def save_ai_preset(self):
        if not self.ai_frames:
            self.notice = "先点「让 AI 画」生成动画预览"
            return
        fps, frames = self.ai_frames
        name = ask_text(self.root, "存为预设", "预设名称：", initial="AI 作品")
        if not name:
            return
        try:
            presetlib.save(name, frames, fps, "ai")
            self.notice = f"AI 作品已存为预设「{name}」"
        except (OSError, ValueError) as exc:
            self.notice = f"保存失败：{exc}"
        self.refresh_mine_gallery()

    def upload_ai(self):
        if not self.ai_frames:
            self.notice = "先点「让 AI 画」生成画面"
            return
        if not self.confirm_upload():
            return
        fps, frames = self.ai_frames
        self.preview_frames = frames
        self.preview_fps = fps
        self.draw_preview()
        self._send_upload()

    def save_ai_settings(self):
        settings = {"base_url": ai_designer.clean_base_url(self.ai_url.get()) or ai_designer.DEFAULT_BASE_URL,
                    "api_key": self.ai_key.get().strip(),
                    "model": self.ai_model.get().strip() or ai_designer.DEFAULT_MODEL}
        try:
            ai_designer.save_settings(self.ai_settings_path, settings)
            self.ai_url.set(settings["base_url"])
            self.notice = "AI 配置已保存"
            self.ai_test_label.config(text=f"已保存 · 模型 {settings['model']}", fg=MUTED)
        except (OSError, ValueError) as exc:
            self.notice = f"保存配置失败：{exc}"

    def test_ai_settings(self):
        settings = {"base_url": ai_designer.clean_base_url(self.ai_url.get()),
                    "api_key": self.ai_key.get().strip(),
                    "model": self.ai_model.get().strip() or ai_designer.DEFAULT_MODEL}
        self.ai_test_label.config(text="正在测试接口…", fg=MUTED)

        def task():
            try:
                text = ai_designer.test_connection(settings)
                self.updates.put(("ai_test", f"连接成功 · 模型回复「{text.strip()[:12]}」"))
            except Exception as exc:
                self.updates.put(("ai_test_error", f"测试失败：{exc}"))
        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- 文本槽
    def select_slot(self, slot: int):
        self.slot_index = slot
        for index, control in enumerate(self.slot_buttons):
            control.configure(bg=PINK if index == slot else PAPER)

    def read_slot_text(self):
        self.device.send("text_backup", None)
        self.slot_hint.config(text="正在读入 5 个文本槽…")

    def write_slot_text(self):
        text = self.text_entry.get()
        if not text:
            self.notice = "先在输入框里写点内容"
            return
        try:
            text.encode("ascii")
        except UnicodeEncodeError:
            self.notice = "文本槽只接受英文、数字及 ASCII 符号"
            return
        if len(text) > 30:
            self.notice = "文本槽最多 30 个字符"
            return
        display_text = text.upper()
        if display_text != text:
            self.text_entry.delete(0, "end")
            self.text_entry.insert(0, display_text)
        self.device.send("text_write", (self.slot_index, display_text))
        self.notice = "正在写入文本槽；小写已转大写以便键盘显示" if display_text != text else "正在写入文本槽…"

    def backup_slots(self):
        self.device.send("text_backup", None)
        self.notice = "正在读取 5 个文本槽…"

    def restore_slots(self):
        source = filedialog.askopenfilename(title="选择文本槽备份",
                                            filetypes=[("文本槽备份", "*.json")])
        if not source:
            return
        try:
            data = json.loads(Path(source).read_text(encoding="utf-8"))
            slots = [str(item) for item in data.get("slots", [])][:5]
            slots += [""] * (5 - len(slots))
        except (OSError, ValueError) as exc:
            self.notice = f"备份文件读取失败：{exc}"
            return
        self.device.send("text_restore", slots)
        self.notice = "正在恢复文本槽…"

    # ---------------------------------------------------------------- 灯光控制
    def apply_screen_brightness(self, value: int):
        self.upload_brightness = int(value)
        self.draw_preview()
        if self.confirm_upload():
            self._send_upload()

    def read_lights(self):
        self.device.send("light_read", None)
        self.notice = "正在读取灯光当前值…"

    def pick_light_color(self, group: str):
        color = ask_color(self.root, f"{lights.GROUPS[group][2]}颜色",
                          initial=self.light_widgets[group]["color"])
        if not color:
            return
        self.light_widgets[group]["color"] = color
        self.light_widgets[group]["color_hsv"] = None

    def apply_lights(self, group: str):
        if not self.device_state.get("connected"):
            self.notice = "请先连接 DP104 键盘"
            return
        if not self.lights_loaded[group]:
            self.read_lights()
            self.notice = "先读取键盘当前灯光设置，完成后再应用"
            return
        channel = lights.GROUPS[group][0]
        widgets = self.light_widgets[group]
        color_args = widgets.get("color_hsv")
        if color_args is None:
            color_args = lights.color_command_args(widgets["color"])
        values = ((1, (int(widgets["brightness"].get()),)),
                  (2, (lights.effect_index(group, widgets["effect"].get()) or 0,)),
                  (3, (int(widgets["speed"].get()),)),
                  (4, color_args))
        self.device.send("light_apply", (group, channel, values))
        self.notice = f"正在应用{lights.GROUPS[group][2]}设置…"

    # ---------------------------------------------------------------- 设置页
    def save_scenes(self):
        config = {"enabled": bool(self.scene_enabled.get()), "rules": self.rules}
        try:
            scheduler.save(self.auto.schedule_path, config)
            self.notice = "定时场景已保存" + ("，已启用" if config["enabled"] else "（未启用）")
            self.auto.last_rule_key = ""
        except OSError as exc:
            self.notice = f"保存场景失败：{exc}"

    def save_milestone(self):
        self.milestones.configure(int(self.milestone_step.get()),
                                  bool(self.milestone_enabled.get()))
        self.notice = (f"里程碑设置已保存：每 {self.milestones.step:,} 键"
                       + ("，已启用" if self.milestones.enabled else "，未启用"))

    def open_data_dir(self):
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606 - user requested local folder

    def _load_scenes(self):
        config = scheduler.load(self.auto.schedule_path)
        self.rules = config.get("rules", [])
        self.scene_enabled.set(bool(config.get("enabled")))
        self.render_rules()

    def render_rules(self):
        self.rule_list.delete(0, "end")
        for rule in self.rules:
            self.rule_list.insert("end", scheduler.rule_line(rule))

    def add_rule(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加定时场景")
        dialog.configure(bg=PAPER)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        start = tk.StringVar(value="09:00")
        end = tk.StringVar(value="18:00")
        label(dialog, "开始 (HH:MM)", 10, INK, True).pack(anchor="w", padx=16, pady=(14, 0))
        pixel_entry(dialog, textvariable=start, font=(PIXEL_FONT, 12),
                    width=10).pack(anchor="w", padx=16)
        label(dialog, "结束 (HH:MM)", 10, INK, True).pack(anchor="w", padx=16, pady=(8, 0))
        pixel_entry(dialog, textvariable=end, font=(PIXEL_FONT, 12),
                    width=10).pack(anchor="w", padx=16)
        label(dialog, "动作：切换内置屏幕模式", 10, INK, True).pack(
            anchor="w", padx=16, pady=(10, 0))
        mode_choice = ttk_combobox(dialog, [name for name, _ in scheduler.MODE_CHOICES], width=10)
        mode_choice.pack(anchor="w", padx=16, pady=(2, 0))

        def confirm():
            rule = {"kind": "mode",
                    "start": start.get().strip(), "end": end.get().strip()}
            picked = mode_choice.get().split(" · ")[-1]
            rule["value"] = next((v for n, v in scheduler.MODE_CHOICES if n == picked), 1)
            self.rules.append(rule)
            self.render_rules()
            dialog.destroy()
        button(dialog, "添加", confirm, tone="primary", pady=5).pack(pady=12)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.lift(self.root)
        dialog.grab_set()
        dialog.focus_force()
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def delete_rule(self):
        selection = self.rule_list.curselection()
        if not selection:
            self.notice = "先在列表里选中一条规则"
            return
        del self.rules[selection[0]]
        self.render_rules()

    # ---------------------------------------------------------------- 托盘
    def _start_tray(self):
        image = Image.new("RGBA", (64, 64), INK)
        drawing = ImageDraw.Draw(image)
        for y, line in enumerate(CAT):
            for x, char in enumerate(line):
                if char != ".":
                    color = PINK if char == "P" else (PAPER if char == "W" else INK)
                    drawing.rectangle((x*4, y*4+5, x*4+3, y*4+8), fill=color)
        menu = pystray.Menu(
            pystray.MenuItem("打开像素小屋", lambda icon, item: self.updates.put(("open", None)), default=True),
            pystray.MenuItem("退出程序", lambda icon, item: self.updates.put(("exit", None))))
        self.tray = pystray.Icon("DP104PixelCottage", image, "DP104 像素小屋", menu)

        def run_tray():
            try:
                def setup(icon):
                    icon.visible = True
                    self.tray_ready.set()
                self.tray.run(setup=setup)
            except Exception as exc:
                self.tray_error = str(exc)
                self.updates.put(("tray_error", self.tray_error))
        threading.Thread(target=run_tray, name="dp104-tray", daemon=True).start()

    def _finish_startup_tray(self):
        if self.tray_ready.is_set():
            return
        if self.tray_error:
            self.show()
            self.notice = f"托盘未能启动，已打开窗口：{self.tray_error}"
            return
        self.root.after(200, self._finish_startup_tray)

    def toggle_startup(self):
        enabled = self.startup_enabled.get()
        try:
            startup.set_enabled(enabled)
        except OSError as exc:
            self.startup_enabled.set(not enabled)
            self.notice = f"修改开机启动失败：{exc}"
            return
        self.notice = "已开启开机启动；登录 Windows 后自动进入托盘" if enabled else "已关闭开机启动"

    def on_minimize(self, event):
        if event.widget is self.root:
            self.root.after(80, lambda: self.hide() if self.root.state() == "iconic" else None)

    def hide(self):
        if self.closing:
            return
        if self.tray_error:
            self.notice = f"托盘图标创建失败：{self.tray_error}"
            self.root.state("normal")
            return
        if not self.tray_ready.is_set():
            self.root.after(100, self.hide)
            return
        self.root.withdraw()
        if not self.tray_hint_shown:
            self.tray_hint_shown = True
            try:
                self.tray.notify("窗口已收进通知区域，双击图标可重新打开。", "DP104 像素小屋")
            except Exception:
                pass

    def show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    # ---------------------------------------------------------------- 主循环
    def _refresh_timer(self):
        if self.closing:
            return
        try:
            self.refresh()
        except Exception as exc:
            self.notice = f"界面刷新失败：{exc}"
            self.notice_label.config(text=self.notice[:100])
        finally:
            if not self.closing:
                self.root.after(500, self._refresh_timer)

    def refresh(self):
        if self.closing:
            return
        while True:
            try:
                kind, value = self.updates.get_nowait()
            except queue.Empty:
                break
            if kind == "device":
                was_connected = self.device_state.get("connected", False)
                self.device_state = value
                if value.get("connected") and not was_connected:
                    self.lights_loaded = {"keys": False, "bar": False}
                    self.device.send("light_read", None)
                elif not value.get("connected"):
                    self.lights_loaded = {"keys": False, "bar": False}
            elif kind == "mode":
                self.mode = value
            elif kind == "brightness":
                self.brightness = value
            elif kind == "notice":
                self.notice = value
            elif kind == "tray_error":
                self.notice = f"托盘不可用：{value}"
            elif kind == "open":
                self.show()
            elif kind == "exit":
                self.close()
                return
            elif kind == "ai_result":
                self.apply_ai_result(value[0], value[1])
            elif kind == "ai_error":
                self.apply_ai_error(value)
            elif kind == "image_result":
                name, frames, fps = value
                self.set_preview_animation(name, frames, fps)
                self.create_status.config(text=f"已导入「{name}」：{len(frames)} 帧 @ {fps} fps，可编辑或上传。")
                self.notice = f"图片已转换成 8×24 像素动画：{name}"
            elif kind == "image_error":
                self.create_status.config(text=f"导入失败：{value}")
                self.notice = f"导入失败：{value}"
            elif kind == "ai_test":
                self.ai_test_label.config(text=f"✓ {value}", fg=MINT)
            elif kind == "ai_test_error":
                self.ai_test_label.config(text=f"✗ {value}", fg=INK)
            elif kind == "text_slots":
                self.apply_text_slots(value)
            elif kind == "milestone":
                self.fire_milestone(value)
            elif kind == "lights_full":
                self.apply_lights_full(value)
            elif kind == "light_value":
                channel, sub, *values = value
                group = "keys" if channel == lights.KEYS else "bar"
                if sub == 1:
                    self.light_widgets[group]["brightness"].set(values[0])
                elif sub == 2:
                    self._sync_effect(group, values[0])
                elif sub == 3:
                    self.light_widgets[group]["speed"].set(values[0])
                elif sub == 4:
                    self._sync_color(group, values)

        connected = self.device_state.get("connected")
        port = self.device_state.get("port")
        serial_ok = self.device_state.get("serial")
        if not connected:
            text, color = "● 等待 DP104", YELLOW
            self.keyboard_label.config(text="键盘还没来")
            self.serial_label.config(text="USB 串口：未连接")
            self.home_device_label.config(text="DP104 未连接")
        else:
            self.keyboard_label.config(text=f"DP104 已连接 · {MODES.get(self.mode, '未知模式')}")
            if serial_ok:
                text, color = f"● {port} 可写", MINT
                self.serial_label.config(text=f"USB 串口：{port} · 可以上传")
                self.home_device_label.config(text=f"DP104 已连接 · {port} 可上传")
            else:
                reason = self.device_state.get("serial_error") or "不可用"
                text, color = f"● {port or '串口'} {reason}", YELLOW
                self.serial_label.config(text=f"USB 串口：{port or '未找到'} · {reason}")
                self.home_device_label.config(text=f"DP104 已连接 · 串口{reason}")
        self.header_status.config(text=text, fg=color)
        for value_, control in self.mode_buttons.items():
            control.configure(bg=BLUE if connected and value_ == self.mode else PAPER)

        snapshot = self.metrics.snapshot()
        self.keys_label.config(text=f"{snapshot['keys_today']:,}")
        self.speed_label.config(text=f"{snapshot['keys_per_minute']} 键/分  ·  约 {snapshot['wpm']} WPM")
        remaining = self.milestones.remaining(snapshot["keys_today"])
        state = "已启用" if self.milestones.enabled else "未启用"
        self.milestone_label.config(
            text=f"里程碑 · 每 {self.milestones.step:,} 键一档（{state}）· 还差 {remaining:,} 键")
        self.draw_keymap()
        self.notice_label.config(text=self.notice[:100])
        if int(time.monotonic()) % 20 == 0:
            try:
                self.metrics.save()
            except OSError:
                pass

    def _sync_effect(self, group: str, index: int):
        option = next((f"{i} · {cn}" for i, cn, _ in lights.GROUPS[group][1] if i == index), None)
        if option:
            self.light_widgets[group]["effect"].set(option)

    def _sync_color(self, group: str, values):
        hue, sat = (values if isinstance(values, (tuple, list)) else (values, 255))[:2]
        import colorsys
        self.light_widgets[group]["color_hsv"] = (hue, sat)
        red, green, blue = colorsys.hsv_to_rgb(hue / 255, sat / 255, 1.0)
        self.light_widgets[group]["color"] = f"#{round(red*255):02x}{round(green*255):02x}{round(blue*255):02x}"

    def apply_lights_full(self, data):
        for group in ("keys", "bar"):
            values = data.get(group, {})
            self.lights_loaded[group] = all(sub in values for sub in (1, 2, 3, 4))
            if 1 in values:
                self.light_widgets[group]["brightness"].set(values[1])
            if 2 in values:
                self._sync_effect(group, values[2])
            if 3 in values:
                self.light_widgets[group]["speed"].set(values[3])
            if 4 in values:
                self._sync_color(group, values[4])
        self.notice = "灯光当前值已读入"

    def draw_keymap(self):
        if not hasattr(self, "keymap"):
            return
        cells, _ = self.metrics.cells_snapshot()
        canvas = self.keymap
        canvas.delete("keys")
        width = max(canvas.winfo_width(), 760)
        height = max(canvas.winfo_height(), 250)
        unit = min((width - 22) / 24.5, (height - 16) / 6.25)
        left = (width - 24.5 * unit) / 2
        top = (height - 6.25 * unit) / 2
        peak = max((cells.get((key.row, key.col), 0) for key in PHYSICAL_KEYS), default=0)
        self.keymap_hitboxes = []
        for key in PHYSICAL_KEYS:
            x1 = left + key.x * unit + 2
            y1 = top + key.y * unit + 2
            x2 = left + (key.x + key.width) * unit - 2
            y2 = top + (key.y + key.height) * unit - 2
            count = cells.get((key.row, key.col), 0)
            selected = self.selected_matrix_key == (key.row, key.col)
            canvas.create_rectangle(x1, y1, x2, y2, fill=heat_color(count, peak),
                                    outline=INK, width=3 if selected else 1, tags="keys")
            font_size = 8 if unit >= 36 else 7
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2 - (4 if count else 0),
                               text=key.legend, fill=INK, font=(PIXEL_FONT, font_size, "bold"),
                               tags="keys")
            if count:
                canvas.create_text(x2 - 4, y2 - 3, text=str(count), anchor="se",
                                   fill=INK, font=(PIXEL_FONT, 7), tags="keys")
            self.keymap_hitboxes.append((x1, y1, x2, y2, key))
        if self.selected_matrix_key in KEY_BY_MATRIX:
            key = KEY_BY_MATRIX[self.selected_matrix_key]
            self.keymap_caption.config(text=f"{key.legend} · 今天按下 {cells.get(self.selected_matrix_key, 0)} 次"
                                            "  |  颜色越深，频率越高；统计只来自 DP104")

    def select_keymap_key(self, event):
        for x1, y1, x2, y2, key in self.keymap_hitboxes:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.selected_matrix_key = (key.row, key.col)
                self.draw_keymap()
                return

    def apply_text_slots(self, slots):
        text = slots[self.slot_index] if self.slot_index < len(slots) else ""
        summary = " / ".join(f"{index + 1}:{item or '空'}" for index, item in enumerate(slots))
        if getattr(self, "pending_slot_export", False):
            self.pending_slot_export = False
            target = filedialog.asksaveasfilename(
                title="备份文本槽", defaultextension=".json", initialfile="文本槽备份.json",
                filetypes=[("文本槽备份", "*.json")], parent=self.root)
            if target:
                try:
                    Path(target).write_text(
                        json.dumps({"slots": list(slots)}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
                    self.notice = f"5 个文本槽已备份到 {target}"
                except OSError as exc:
                    self.notice = f"备份失败：{exc}"
            self.slot_hint.config(text=f"5 槽内容：{summary[:90]}")
            return
        self.text_entry.delete(0, "end")
        self.text_entry.insert(0, text)
        self.slot_hint.config(text=f"当前槽 {self.slot_index + 1} 已读入 ｜ 5 槽：{summary[:80]}")
        self.notice = "文本槽已读入"

    def export_slots(self):
        self.pending_slot_export = True
        self.device.send("text_backup", None)
        self.notice = "读取完成后会弹出备份对话框…"

    def fire_milestone(self, keys: int):
        state = self.device_state
        if not state.get("connected") or not state.get("serial") or self.mode == 2:
            self.notice = f"达到 {keys:,} 键里程碑；屏幕不在可推送状态，本次不推送"
            return
        frames = make_milestone_frames(keys)
        self.device.send("animation", (frames, 6))
        self.notice = f"🎉 达到 {keys:,} 键，已推送庆祝画面"

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.device.stop()
        self.auto.stop()
        try:
            self.metrics.save()
        except OSError:
            pass
        if self.tray:
            self.tray.stop()
        if self.mutex and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self.mutex)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def ttk_combobox(parent, values, width):
    return PixelDropdown(parent, values, width)


if __name__ == "__main__":
    try:
        lock = acquire_mutex()
        Studio(lock).run()
    except Exception as exc:
        messagebox.showerror("DP104 像素小屋", str(exc))
