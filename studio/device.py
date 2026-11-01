"""Single-owner DP104 HID worker and explicit one-shot animation transfer."""

from __future__ import annotations

import ctypes
import queue
import threading
import time

import hid
import serial
from serial.tools import list_ports

from .effects import COLS, ROWS, MAX_FRAMES
from .lights import GROUPS as _LIGHT_GROUPS
from . import textslots

VID, PID = 0xE560, 0xE104
MODES = {0: "熄屏", 1: "打字", 2: "自定义", 3: "连接状态", 4: "烟花", 5: "音乐", 6: "滚动"}


class TransferError(RuntimeError):
    """The CDC transfer did not finish with valid firmware acknowledgements."""


def normalize_serial(value) -> str:
    return "".join(c for c in str(value or "").upper() if c.isalnum())


def find_keyboard() -> dict | None:
    matches = [item for item in hid.enumerate(VID, PID)
               if item.get("interface_number") == 1 and item.get("usage_page") == 0xFF60]
    matches.sort(key=lambda item: (normalize_serial(item.get("serial_number")), str(item.get("path"))))
    return matches[0] if matches else None


def find_port(keyboard: dict) -> str | None:
    serial_id = normalize_serial(keyboard.get("serial_number"))
    matches = [port for port in list_ports.comports()
               if port.vid == VID and port.pid == PID
               and normalize_serial(port.serial_number) == serial_id]
    matches.sort(key=lambda port: port.device)
    return matches[0].device if matches else None


def hid_command(device, payload: list[int], timeout: float = 0.25) -> bytes | None:
    if not payload or len(payload) > 32:
        raise ValueError("HID 命令必须为 1-32 字节")
    device.write([0] + (payload + [0] * 32)[:32])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = device.read(64)
        if result:
            reply = bytes(result)
            expected = bytes(payload if payload[0] == 0xD1 else
                             payload[:3] if payload[0] in (0x07, 0x08) else payload[:2])
            if reply[0] == 0xFF or reply[:len(expected)] == expected:
                return reply
        time.sleep(0.004)
    return None


def get_cdc_support(device) -> bool | None:
    """Online vendor configurator's read-only 0xBF capability query.

    False selects HID upload; an unsupported query keeps legacy CDC behavior.
    Protocol reference: cfg.ticktype.com/assets/index-e0db734e.js (2026-11-01).
    """
    reply = hid_command(device, [0xBF], timeout=0.6)
    if reply is None or len(reply) < 2 or reply[0] != 0xBF:
        return None
    return bool(reply[1])


def upload_animation_hid(device, frames: list[bytes], fps: int = 8) -> dict:
    """Match the current vendor web configurator's D1/30, D1/31 transport.

    This uploads animation data only; firmware commands D1/10 and D1/11
    are deliberately not implemented. No screen mode changes are sent.
    """
    if not frames or len(frames) > MAX_FRAMES or any(len(frame) != ROWS * COLS * 3 for frame in frames):
        raise ValueError(f"动画需为 1-{MAX_FRAMES} 帧，每帧 8×24 HSV")
    if not 1 <= fps <= 30:
        raise ValueError("帧率无效")

    header = [0xD1, 0x30, len(frames), fps, ROWS, COLS]
    reply = hid_command(device, header, timeout=2)
    if reply is None or len(reply) != 32 or reply[:len(header)] != bytes(header):
        raise TransferError("HID 动画帧头未获完整确认")
    if reply[-1]:
        raise TransferError(f"HID 动画帧头被拒绝，错误码 {reply[-1]}")

    data = b"".join(frames)
    chunks = 0
    for offset in range(0, len(data), 25):
        chunk = data[offset:offset + 25]
        payload = [0xD1, 0x31, *offset.to_bytes(4, "big"), len(chunk), *chunk]
        reply = hid_command(device, payload, timeout=1)
        # Full echoed payload checks the offset, size and the final pixel byte.
        if reply is None or len(reply) != 32 or reply[:len(payload)] != bytes(payload):
            raise TransferError(f"HID 动画分片 {offset} 未获完整确认")
        chunks += 1
    return {"frames": len(frames), "chunks": chunks, "acknowledged": True, "transport": "hid"}


def set_light_value(device, channel: int, sub: int, args: tuple[int, ...]) -> tuple[bytes, bool]:
    """Avoid rewriting unchanged values; some firmware values quantize on SET."""
    current = hid_command(device, [0x08, channel, sub])
    if current and current[0] != 0xFF and current[3:3 + len(args)] == bytes(args):
        return current, False
    result = hid_command(device, [0x07, channel, sub, *args])
    if result is None or result[0] == 0xFF:
        raise RuntimeError("键盘未接受该灯光设置")
    verified = hid_command(device, [0x08, channel, sub])
    if verified is None or verified[0] == 0xFF or len(verified) < 3 + len(args):
        raise RuntimeError("灯光设置后无法读取键盘回执")
    return verified, True


def save_light_channel(device, channel: int) -> None:
    """DP104 updates physical LEDs only after the VIA custom-save command."""
    result = hid_command(device, [0x09, channel], timeout=0.6)
    if result is None or result[0] == 0xFF or result[:2] != bytes((0x09, channel)):
        raise RuntimeError("键盘未确认灯光保存命令")


def select_screen_mode(device, mode: int) -> None:
    if mode not in MODES:
        raise ValueError("无效的屏幕模式")
    current = hid_command(device, [0x08, 26, 2])
    if current is None or len(current) < 4 or current[0] == 0xFF:
        raise RuntimeError("无法读取当前屏幕模式")
    # Avoid reloading a mode already selected, especially a freshly uploaded DIY.
    if current[3] == mode:
        return
    result = hid_command(device, [0x07, 26, 2, mode])
    if result is None or result[0] == 0xFF:
        raise RuntimeError("键盘未接受屏幕模式")
    save_light_channel(device, 26)
    actual = hid_command(device, [0x08, 26, 2])
    if actual is None or len(actual) < 4 or actual[0] == 0xFF or actual[3] != mode:
        raise RuntimeError("屏幕模式切换后回读不一致")


def cdc_command(port, command: int, args: bytes = b"", response: bool = True) -> bytes | None:
    if len(args) > 63:
        raise ValueError("CDC payload too long")
    packet = bytes([command]) + args + bytes(63 - len(args))
    port.write(packet)
    if not response:
        return None
    deadline = time.monotonic() + 2.5
    pending = bytearray()
    while time.monotonic() < deadline:
        pending.extend(port.read(64 - len(pending)))
        if len(pending) == 64:
            if pending[0] == command:
                return bytes(pending)
            pending.clear()
    return None


def probe_serial(port_name: str) -> None:
    """A harmless write checks the endpoint, not just Windows COM enumeration."""
    with serial.Serial(port_name, 115200, timeout=0.2, write_timeout=0.8) as port:
        port.reset_output_buffer()
        cdc_command(port, 0xEF, response=False)


def upload_animation(port_name: str, frames: list[bytes], fps: int = 8) -> dict:
    if not frames or len(frames) > MAX_FRAMES or any(len(frame) != ROWS * COLS * 3 for frame in frames):
        raise ValueError(f"动画需为 1-{MAX_FRAMES} 帧，每帧 8×24 HSV")
    if not 1 <= fps <= 30:
        raise ValueError("帧率无效")
    with serial.Serial(port_name, 115200, timeout=0.2, write_timeout=1.0) as port:
        port.reset_output_buffer()
        port.reset_input_buffer()
        started = False
        chunks_sent = 0
        try:
            header = cdc_command(port, 0xC0, bytes([len(frames), fps, ROWS, COLS]))
            if header is None or header[5] == 0xEE:
                raise TransferError("键盘串口未接受动画帧头")
            started = True
            needs_ack = bool(header[6])
            for index, frame in enumerate(frames):
                for offset in range(0, len(frame), 56):
                    chunk = frame[offset:offset + 56]
                    absolute = index * len(frame) + offset
                    body = absolute.to_bytes(4, "big") + bytes([len(chunk)]) + chunk
                    reply = cdc_command(port, 0xC1, body, response=needs_ack)
                    if needs_ack and (reply is None or reply[5] == 0xEE
                                      or reply[1:5] != absolute.to_bytes(4, "big")
                                      or reply[5] != len(chunk)):
                        raise TransferError(f"键盘未确认动画分片 {index}:{offset}")
                    chunks_sent += 1
        except Exception:
            if started:
                try:
                    cdc_command(port, 0xE2, response=False)
                except (OSError, serial.SerialException):
                    pass
            raise
    return {"frames": len(frames), "chunks": chunks_sent, "acknowledged": needs_ack}


def send_animation(device, port_name: str, frames: list[bytes], fps: int = 8) -> dict:
    """Select CUSTOM before transfer; reselecting afterward overwrites fresh frames."""
    current = hid_command(device, [0x08, 26, 2])
    if current is None or current[0] == 0xFF or len(current) < 4:
        raise RuntimeError("无法读取屏幕模式")
    previous_mode = current[3]
    if previous_mode != 2:
        changed = hid_command(device, [0x07, 26, 2, 2])
        if changed is None or changed[0] == 0xFF:
            raise RuntimeError("无法切换自定义屏幕模式")
        time.sleep(0.2)
    try:
        receipt = upload_animation(port_name, frames, fps)
    except Exception:
        if previous_mode != 2:
            try:
                hid_command(device, [0x07, 26, 2, previous_mode])
            except (OSError, RuntimeError):
                pass
        raise
    mode_after = None
    for attempt in range(4):
        mode_after = hid_command(device, [0x08, 26, 2], timeout=0.5)
        if mode_after is not None and mode_after[0] != 0xFF and len(mode_after) >= 4:
            break
        if attempt < 3:
            time.sleep(0.15)
    if mode_after is None or mode_after[0] == 0xFF or len(mode_after) < 4 or mode_after[3] != 2:
        raise RuntimeError("帧已传输，但键盘未保持自定义屏幕模式")
    return receipt


class DeviceWorker:
    def __init__(self, metrics, updates: queue.Queue):
        self.metrics = metrics
        self.updates = updates
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.serial_failed = False
        self.transport = "cdc"
        self.serial_error = ""
        self.last_serial_probe = 0.0
        self.uploading = False
        self.thread = threading.Thread(target=self._run, name="dp104-device", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)

    def send(self, action: str, value) -> None:
        self.commands.put((action, value))

    def _serial_state(self, descriptor: dict, device=None) -> dict:
        if device is not None and get_cdc_support(device) is False:
            self.transport = "hid"
            self.serial_failed = False
            self.serial_error = ""
            self.last_serial_probe = time.monotonic()
            return {"connected": True, "serial": False, "port": None,
                    "serial_error": "", "transport": "hid", "upload_ready": True}
        self.transport = "cdc"
        port = find_port(descriptor)
        state = {"connected": True, "serial": False, "port": port,
                 "serial_error": "", "transport": "cdc", "upload_ready": False}
        if port:
            try:
                probe_serial(port)
                self.serial_failed = False
                self.serial_error = ""
                state["serial"] = True
                state["upload_ready"] = True
            except (OSError, serial.SerialException) as exc:
                self.serial_failed = True
                self.serial_error = "写入超时" if isinstance(exc, serial.SerialTimeoutException) else "无法打开"
                state["serial_error"] = self.serial_error
        else:
            self.serial_failed = True
            self.serial_error = "未检测到串口"
            state["serial_error"] = self.serial_error
        self.last_serial_probe = time.monotonic()
        return state

    @staticmethod
    def _restart_usb(descriptor: dict) -> None:
        serial_id = str(descriptor.get("serial_number") or "").replace(" ", "_")
        if not serial_id or len(serial_id) > 64 or not all(c.isalnum() or c in "_-" for c in serial_id):
            raise RuntimeError("无法安全确定键盘 USB 实例")
        instance = f"USB\\VID_{VID:04X}&PID_{PID:04X}\\{serial_id}"
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "pnputil.exe", f'/restart-device "{instance}"', None, 0)
        if result <= 32:
            raise RuntimeError("未能启动 USB 设备重启；可手动拔插键盘")

    def _run(self) -> None:
        while not self.stop_event.is_set():
            descriptor = find_keyboard()
            if not descriptor:
                self.serial_failed = False
                self.serial_error = ""
                while True:
                    try:
                        self.commands.get_nowait()
                        self.updates.put(("notice", "键盘未连接，操作未执行"))
                    except queue.Empty:
                        break
                self.metrics.reset_matrix()
                self.updates.put(("device", {"connected": False, "serial": False}))
                self.stop_event.wait(2)
                continue
            device = hid.device()
            try:
                device.open_path(descriptor["path"])
                device.set_nonblocking(1)
                self.updates.put(("device", self._serial_state(descriptor, device)))
                last_settings = 0.0
                while not self.stop_event.is_set():
                    try:
                        action, value = self.commands.get_nowait()
                    except queue.Empty:
                        action = None
                    if action == "mode":
                        select_screen_mode(device, int(value))
                        self.updates.put(("mode", int(value)))
                    elif action == "brightness":
                        result = hid_command(device, [0x07, 26, 1, int(value)])
                        if result is None or result[0] == 0xFF:
                            raise RuntimeError("键盘未接受亮度")
                        save_light_channel(device, 26)
                        actual = hid_command(device, [0x08, 26, 1])
                        self.updates.put(("brightness", int(value)))
                        self.updates.put(("notice", f"屏幕亮度已应用（键盘回读 {actual[3]}）"
                                          if actual and len(actual) >= 4 else "屏幕亮度已应用"))
                    elif action == "animation":
                        frames, fps = value if isinstance(value, tuple) and len(value) == 2 \
                            else (value, 8)
                        if self.serial_failed:
                            self.updates.put(("device", self._serial_state(descriptor, device)))
                        if self.serial_failed:
                            self.updates.put(("notice", f"串口 {find_port(descriptor) or '未发现'} {self.serial_error}；可重启 USB 或拔插键盘"))
                            continue
                        port = find_port(descriptor) if self.transport == "cdc" else None
                        if self.transport == "cdc" and not port:
                            self.updates.put(("notice", "未找到与此键盘匹配的串口"))
                            continue
                        self.updates.put(("notice", "正在传输画面，请勿拔出键盘…"))
                        self.uploading = True
                        try:
                            receipt = (upload_animation_hid(device, frames, fps) if self.transport == "hid"
                                       else send_animation(device, port, frames, fps))
                        except (OSError, serial.SerialException, RuntimeError) as exc:
                            if self.transport == "cdc" and isinstance(exc, (OSError, serial.SerialException, TransferError)):
                                self.serial_failed = True
                                self.serial_error = "写入超时" if isinstance(exc, serial.SerialTimeoutException) else "上传失败"
                                self.updates.put(("device", {"connected": True, "serial": False, "port": port,
                                                             "serial_error": self.serial_error}))
                            recovery = ("请退出其他键盘控制程序后重新探测；画面可能只写入了一部分"
                                        if self.transport == "hid" else
                                        "若串口写入超时，请拔插 USB；再次上传前先将实体屏幕切到预设画面")
                            self.updates.put(("notice", f"画面上传未完成验证：{exc}。{recovery}"))
                            continue
                        finally:
                            self.uploading = False
                        if self.transport == "cdc":
                            self.updates.put(("mode", 2))
                        confirmation = "分片回执已核对" if receipt["acknowledged"] else "固件未启用分片回执"
                        self.updates.put(("notice", f"已通过 {self.transport.upper()} 传输 {receipt['frames']} 帧，{confirmation}；点「DIY / 自定义」播放"))
                    elif action == "text_write":
                        slot, text = value
                        if textslots.write_slot(device, int(slot), str(text), hid_command):
                            save_light_channel(device, 26)
                            actual = textslots.read_slot(device, int(slot), hid_command)
                            if actual != str(text):
                                self.updates.put(("notice", "文本槽写入后回读不一致"))
                            else:
                                self.updates.put(("notice", f"文本槽 {int(slot) + 1} 已保存；切到滚动模式查看"))
                        else:
                            self.updates.put(("notice", "文本槽写入被键盘拒绝"))
                    elif action == "text_backup":
                        self.updates.put(("text_slots", textslots.read_all(device, hid_command)))
                    elif action == "text_restore":
                        ok = all(textslots.write_slot(device, slot, text, hid_command)
                                 for slot, text in enumerate(value))
                        if ok:
                            save_light_channel(device, 26)
                        self.updates.put(("notice", "5 个文本槽已恢复" if ok else "文本槽恢复被键盘拒绝"))
                    elif action == "light_set":
                        channel, sub, args = value
                        reply, changed = set_light_value(device, channel, sub, args)
                        if changed:
                            save_light_channel(device, channel)
                        if reply and reply[0] != 0xFF and len(reply) >= 4:
                            span = 2 if sub == 4 and len(reply) >= 5 else 1
                            self.updates.put(("light_value", (channel, sub,
                                                              *(reply[3:3 + span]))))
                    elif action == "light_apply":
                        group, channel, values = value
                        try:
                            changed = False
                            for sub, args in values:
                                if args is None:
                                    continue
                                _, updated = set_light_value(device, channel, sub, args)
                                changed |= updated
                            if changed:
                                save_light_channel(device, channel)
                            for sub, args in values:
                                if args is None:
                                    continue
                                reply = hid_command(device, [0x08, channel, sub])
                                span = 2 if sub == 4 else 1
                                if not reply or reply[0] == 0xFF or len(reply) < 3 + span:
                                    raise RuntimeError("保存后无法读取灯光状态")
                                self.updates.put(("light_value", (channel, sub,
                                                                  *(reply[3:3 + span]))))
                        except Exception as exc:
                            self.updates.put(("notice", f"{_LIGHT_GROUPS[group][2]}设置失败：{exc}"))
                        else:
                            state = "已保存并应用" if changed else "与键盘当前设置相同"
                            self.updates.put(("notice", f"{_LIGHT_GROUPS[group][2]}{state}"))
                    elif action == "light_read":
                        data = {"screen": None, "bar": {}, "keys": {}}
                        reply = hid_command(device, [0x08, 26, 1])
                        if reply and reply[0] != 0xFF and len(reply) >= 4:
                            data["screen"] = reply[3]
                        for group, (channel, _, _) in _LIGHT_GROUPS.items():
                            for sub in (1, 2, 3, 4):
                                item = hid_command(device, [0x08, channel, sub])
                                if item is None or item[0] == 0xFF or len(item) < 4:
                                    continue
                                data[group][sub] = (item[3], item[4]) \
                                    if sub == 4 and len(item) >= 5 else item[3]
                        self.updates.put(("lights_full", data))
                    elif action == "probe_serial":
                        self.updates.put(("device", self._serial_state(descriptor, device)))
                        self.updates.put(("notice", "HID 上传通道可用，无需串口" if self.transport == "hid" else
                                          "串口已恢复" if not self.serial_failed else
                                          f"串口仍不可用：{self.serial_error}"))
                    elif action == "restart_usb":
                        self._restart_usb(descriptor)
                        self.updates.put(("notice", "请确认 Windows 管理员提示；键盘将短暂断开"))
                        self.serial_failed = False
                        self.last_serial_probe = 0
                    matrix = hid_command(device, [0x02, 0x03], timeout=0.15)
                    if matrix is not None and len(matrix) >= 4 and matrix[0] == 0x02:
                        self.metrics.observe(matrix[3:])
                    now = time.monotonic()
                    if self.serial_failed and now - self.last_serial_probe > 60:
                        self.updates.put(("device", self._serial_state(descriptor, device)))
                    if now - last_settings > 5:
                        last_settings = now
                        mode = hid_command(device, [0x08, 26, 2])
                        if mode and len(mode) > 3 and mode[0] != 0xFF:
                            self.updates.put(("mode", mode[3]))
                        brightness = hid_command(device, [0x08, 26, 1])
                        if brightness and len(brightness) > 3 and brightness[0] != 0xFF:
                            self.updates.put(("brightness", brightness[3]))
                    self.stop_event.wait(0.03)
            except Exception as exc:
                self.updates.put(("notice", f"键盘通信：{exc}"))
                self.metrics.reset_matrix()
                self.updates.put(("device", {"connected": False, "serial": False}))
                self.stop_event.wait(2)
            finally:
                try:
                    device.close()
                except Exception:
                    pass
