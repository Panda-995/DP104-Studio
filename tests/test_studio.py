import json
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests

from studio import ai_designer, lights, presetlib, scheduler
from studio.device import (find_keyboard, find_port, hid_command, save_light_channel,
                           send_animation, set_light_value, upload_animation)
from studio.effects import EFFECTS, dim_frames, make_frames
from studio.metrics import TypingMetrics
from studio.keymap import KEYS as PHYSICAL_KEYS, KEY_BY_MATRIX, heat_color
from studio.milestones import Milestones
from studio.presets import make_milestone_frames, render_text
from studio.textslots import encode_slot, read_slot, segments, write_slot


class StudioTests(unittest.TestCase):
    def test_ai_url_normalizes_pasted_newline_and_accepts_full_endpoint(self):
        base = "https://api2.laozhang.ai\n/v1"
        self.assertEqual(ai_designer.chat_url(base),
                         "https://api2.laozhang.ai/v1/chat/completions")
        self.assertEqual(ai_designer.chat_url("https://example.com/v1/chat/completions"),
                         "https://example.com/v1/chat/completions")
        with self.assertRaises(ValueError):
            ai_designer.chat_url("not a URL")

    def test_ai_connection_uses_compatible_minimal_request(self):
        reply = SimpleNamespace(ok=True, status_code=200, json=lambda: {
            "choices": [{"message": {"content": "成功"}}]})
        settings = {"base_url": "https://example.com/v1", "api_key": "test-key", "model": "glm-5.1"}
        with patch("studio.ai_designer.requests.post", return_value=reply) as post:
            self.assertEqual(ai_designer.test_connection(settings), "成功")
        self.assertEqual(post.call_args.args[0], "https://example.com/v1/chat/completions")
        self.assertEqual(set(post.call_args.kwargs["json"]), {"model", "messages", "max_tokens"})
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 64)
        self.assertEqual(post.call_args.kwargs["proxies"], {"http": "", "https": ""})

    def test_ai_falls_back_to_system_proxy_only_when_direct_connection_fails(self):
        reply = SimpleNamespace(ok=True, status_code=200, json=lambda: {
            "choices": [{"message": {"content": "成功"}}]})
        settings = {"base_url": "https://example.com/v1", "api_key": "", "model": "test"}
        with patch("studio.ai_designer.requests.post",
                   side_effect=[requests.ConnectionError("direct unavailable"), reply]) as post:
            self.assertEqual(ai_designer.test_connection(settings), "成功")
        self.assertEqual(post.call_count, 2)
        self.assertIn("proxies", post.call_args_list[0].kwargs)
        self.assertNotIn("proxies", post.call_args_list[1].kwargs)

    def test_ai_chat_accepts_streamed_chunks(self):
        text = json.dumps({"fps": 6, "palette": {"A": "#FF3366"},
                           "frames": [["A" * 24] * 8, ["." * 24] * 8]})
        chunks = [text[:90], text[90:]]
        response = SimpleNamespace(ok=True, status_code=200,
                                   headers={"Content-Type": "text/event-stream"},
                                   iter_lines=lambda: iter(
                                       [("data: " + json.dumps({"choices": [{"delta": {"content": part}}]})).encode()
                                        for part in chunks] + [b"data: [DONE]"]),
                                   close=lambda: None)
        settings = {"base_url": "https://example.com/v1", "api_key": "", "model": "model"}
        with patch("studio.ai_designer.requests.post", return_value=response) as post:
            streamed = ai_designer._chat(settings, [{"role": "user", "content": "pink heart"}], 30, 900,
                                         stream=True)
        self.assertEqual(streamed, text)
        self.assertTrue(post.call_args.kwargs["json"]["stream"])

    def test_ai_rejects_single_frame_and_duplicate_frames(self):
        row = "." * 8 + "A" * 8 + "." * 8
        with self.assertRaisesRegex(ValueError, "不能只返回一张图"):
            ai_designer.parse_design(json.dumps({"fps": 8, "palette": {"A": "#FF3366"},
                                                 "frames": [[row] * 8]}))
        with self.assertRaisesRegex(ValueError, "完全相同"):
            ai_designer.parse_design(json.dumps({"fps": 8, "palette": {"A": "#FF3366"},
                                                 "frames": [[row] * 8, [row] * 8]}))

    def test_ai_scroll_expands_to_looping_frames(self):
        canvas = ["A" + "." * 30] + ["." * 31] * 7
        fps, frames = ai_designer.parse_design(json.dumps({
            "kind": "scroll", "fps": 8, "direction": "left",
            "palette": {"A": "#FF3366"}, "canvas": canvas}))
        self.assertEqual(fps, 8)
        self.assertEqual(len(frames), 31)
        self.assertTrue(all(len(frame) == 576 for frame in frames))
        self.assertNotEqual(frames[0], frames[1])
        self.assertEqual(frames[0][:3], frames[-1][3:6])

    def test_ai_rabbit_sprite_moves_out_and_back_on_physical_grid(self):
        sprite = ["...AA..AA...", "...AB..BA...", "....AAAA....",
                  "...AAAACAA..", "..AAAAAAAAA.", ".AAAAAAAAAA.", "AA.AA..AA..."]
        design = {"kind": "sprite", "subject": "兔子", "fps": 8,
                  "motion": "hop_pingpong", "palette": {"A": "#FFF3E6",
                  "B": "#FF99B3", "C": "#333355"}, "sprite": sprite}
        ai_designer._validate_subject("小兔子左右跳", design)
        fps, frames = ai_designer.parse_design(json.dumps(design))
        self.assertEqual((fps, len(frames)), (8, 24))
        self.assertEqual(len(set(frames)), 24)
        self.assertTrue(all(len(frame) == 8 * 24 * 3 for frame in frames))
        self.assertNotEqual(frames[0], frames[12])
        self.assertNotEqual(frames[0], frames[23])

    def test_ai_multi_pose_sprite_keeps_shape_and_expands_to_long_loop(self):
        base = ["..AA..AA..", ".AAAAAAAA.", ".AABBAAAA.",
                ".AAAAAAAA.", "..AAAAAA..", ".AA....AA."]
        poses = []
        for index in range(6):
            pose = base.copy()
            pose[3] = pose[3][:index + 1] + "B" + pose[3][index + 2:]
            poses.append(pose)
        design = {"kind": "sprite", "subject": "兔子", "fps": 12,
                  "motion": "hop_pingpong", "palette": {"A": "#FFF3E6", "B": "#333355"},
                  "poses": poses,
                  "background": ["." * 24] * 7 + ["." * 23 + "B"]}
        ai_designer._validate_subject("兔子从左跳到右再回来", design)
        fps, frames = ai_designer.parse_design(json.dumps(design))
        self.assertEqual((fps, len(frames)), (12, 64))
        self.assertGreater(len(set(frames)), 30)
        self.assertEqual(frames[0][2], 0)
        self.assertGreater(frames[0][-1], 0)

    def test_ai_storyboard_holds_distinct_keyframes(self):
        frames = [["." * x + "A" + "." * (23 - x)] + ["." * 24] * 7
                  for x in range(8)]
        design = {"kind": "frames", "fps": 10, "palette": {"A": "#FF3366"},
                  "frames": frames, "holds": [4] * 8}
        fps, result = ai_designer.parse_design(json.dumps(design))
        self.assertEqual((fps, len(result)), (10, 32))
        self.assertEqual(len(set(result)), 8)
        self.assertEqual(result[0], result[3])
        self.assertNotEqual(result[3], result[4])

    def test_ai_quality_rejects_single_pixel_pose_variations(self):
        base = ["..AA..AA..", ".AAAAAAAA.", ".AABBAAAA.",
                ".AAAAAAAA.", "..AAAAAA..", ".AA....AA."]
        poses = []
        for index in range(6):
            pose = base.copy()
            pose[3] = pose[3][:index + 1] + "B" + pose[3][index + 2:]
            poses.append(pose)
        data = {"kind": "sprite", "fps": 12, "motion": "hop_pingpong",
                "palette": {"A": "#FFFFFF", "B": "#FF0000"}, "poses": poses}
        _, frames = ai_designer.parse_design(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "肢体或轮廓"):
            ai_designer._validate_animation_quality("兔子跳跃", data, frames)

    def test_ai_rejects_rabbit_without_two_ears(self):
        design = {"kind": "sprite", "sprite": ["....AAAA...."] * 7}
        with self.assertRaisesRegex(ValueError, "两只分开"):
            ai_designer._validate_subject("小兔子跳跃", design)

    def test_ai_generation_retries_nonanimated_reply(self):
        static = json.dumps({"palette": {"A": "#FF3366"}, "frames": [["A" * 24] * 8]})
        animated = json.dumps({"fps": 8, "palette": {"A": "#FF3366"},
                               "frames": [["." * index + "A" + "." * (23 - index)] * 8
                                          for index in range(8)], "holds": [4] * 8})
        settings = {"base_url": "https://example.com/v1", "api_key": "", "model": "test"}
        with patch("studio.ai_designer._chat", side_effect=[static, animated]) as chat:
            fps, frames = ai_designer.generate(settings, "moving heart")
        self.assertEqual((fps, len(frames)), (8, 32))
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(chat.call_args.args[3], 6000)

    def test_light_set_skips_unchanged_firmware_value(self):
        current = bytes([8, lights.KEYS, 1, 248])
        with patch("studio.device.hid_command", return_value=current) as command:
            self.assertEqual(set_light_value(None, lights.KEYS, 1, (248,)), (current, False))
        command.assert_called_once_with(None, [8, lights.KEYS, 1])

    def test_light_set_reads_back_changed_value(self):
        initial = bytes([8, lights.KEYS, 1, 248])
        ack = bytes([7, lights.KEYS, 1, 200])
        updated = bytes([8, lights.KEYS, 1, 200])
        with patch("studio.device.hid_command", side_effect=[initial, ack, updated]) as command:
            self.assertEqual(set_light_value(None, lights.KEYS, 1, (200,)), (updated, True))
        self.assertEqual([call.args[1] for call in command.call_args_list],
                         [[8, lights.KEYS, 1], [7, lights.KEYS, 1, 200], [8, lights.KEYS, 1]])

    def test_light_save_commits_the_channel(self):
        with patch("studio.device.hid_command", return_value=bytes([9, lights.KEYS])) as command:
            save_light_channel(None, lights.KEYS)
        command.assert_called_once_with(None, [9, lights.KEYS], timeout=0.6)

    def test_light_readback_accepts_worker_color_and_waits_for_initial_values(self):
        studio = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.pyw"),
                                run_name="dp104_test")["Studio"]
        ui = SimpleNamespace(light_widgets={"keys": {"color": "#000000"}},
                             device_state={"connected": True},
                             lights_loaded={"keys": False}, notice="")
        studio._sync_color(ui, "keys", [120, 200])
        self.assertNotEqual(ui.light_widgets["keys"]["color"], "#000000")
        self.assertEqual(ui.light_widgets["keys"]["color_hsv"], (120, 200))
        calls = []
        ui.read_lights = lambda: calls.append("read")
        studio.apply_lights(ui, "keys")
        self.assertEqual(calls, ["read"])
        self.assertIn("读取", ui.notice)
        ui.lights_loaded["keys"] = True
        ui.device = SimpleNamespace(send=lambda *args: calls.append(args))
        ui.light_widgets["keys"].update({
            "brightness": SimpleNamespace(get=lambda: 180),
            "effect": SimpleNamespace(get=lambda: lights.effect_options("keys")[6]),
            "speed": SimpleNamespace(get=lambda: 80),
        })
        studio.apply_lights(ui, "keys")
        applied = [item[1] for item in calls if isinstance(item, tuple) and item[0] == "light_apply"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][0:2], ("keys", lights.KEYS))
        self.assertIn((2, (6,)), applied[0][2])
        self.assertIn((4, (120, 200)), applied[0][2])

    def test_manual_text_slot_uppercases_firmware_invisible_lowercase(self):
        studio = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.pyw"),
                                run_name="dp104_text_test")["Studio"]

        class Entry:
            value = "aBc 123"

            def get(self):
                return self.value

            def delete(self, *_):
                self.value = ""

            def insert(self, _, value):
                self.value = value

        calls = []
        ui = SimpleNamespace(text_entry=Entry(), slot_index=1, notice="",
                             device=SimpleNamespace(send=lambda *args: calls.append(args)))
        studio.write_slot_text(ui)
        self.assertEqual(ui.text_entry.value, "ABC 123")
        self.assertEqual(calls, [("text_write", (1, "ABC 123"))])

    def test_matrix_counts_rising_edges_and_never_saves_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "typing.json"
            metrics = TypingMetrics(path)
            metrics.observe(bytes([0, 0]))
            metrics.observe(bytes([3, 0]))
            metrics.observe(bytes([3, 0]))
            metrics.observe(bytes([0, 0]))
            metrics.observe(bytes([1, 0]))
            self.assertEqual(metrics.snapshot()["keys_today"], 3)
            cells, cols = metrics.cells_snapshot()
            self.assertEqual(sum(cells.values()), 3)
            metrics.save()
            self.assertEqual(set(json.loads(path.read_text())), {"day", "keys", "cells"})
            self.assertEqual(TypingMetrics(path).cells_snapshot()[0], cells)

    def test_matrix_high_byte_first_matches_physical_a_key(self):
        with tempfile.TemporaryDirectory() as folder:
            metrics = TypingMetrics(Path(folder) / "typing.json")
            metrics.observe(bytes(14))
            pressed = bytearray(14)
            pressed[7] = 1 << 1  # VIA row 3, col 1 (A): high byte precedes low byte.
            metrics.observe(bytes(pressed))
            self.assertEqual(metrics.cells_snapshot()[0], {(3, 1): 1})
            metrics.day = "2000-01-01"
            self.assertEqual(metrics.snapshot()["keys_today"], 0)
            self.assertEqual(metrics.cells_snapshot()[0], {})

    def test_physical_keyboard_layout_has_104_unique_switches(self):
        self.assertEqual(len(PHYSICAL_KEYS), 104)
        self.assertEqual(len(KEY_BY_MATRIX), 104)
        self.assertEqual(KEY_BY_MATRIX[(3, 1)].legend, "A")
        self.assertEqual(KEY_BY_MATRIX[(6, 5)].legend, "Num")
        self.assertNotEqual(heat_color(0, 10), heat_color(10, 10))

    def test_effect_frames_match_keyboard_dimensions(self):
        self.assertIn("心电图", EFFECTS)
        self.assertNotIn("流星", EFFECTS)
        for effect in EFFECTS:
            frames = make_frames(effect)
            self.assertEqual(len(frames), 8)
            self.assertTrue(all(len(frame) == 8 * 24 * 3 for frame in frames))
            self.assertGreater(len(set(frames)), 1, effect)

    def test_diy_brightness_scales_uploaded_pixel_values(self):
        original = make_frames("心跳")
        dimmed = dim_frames(original, 128)
        self.assertEqual(len(dimmed), len(original))
        self.assertTrue(all(len(frame) == 576 for frame in dimmed))
        self.assertTrue(all(dimmed[0][index] <= original[0][index]
                            for index in range(2, 576, 3)))
        self.assertLess(sum(dimmed[0][2::3]), sum(original[0][2::3]))
        self.assertEqual(dim_frames(original, 255), original)

    def test_milestone_frames_loop_and_fit_screen(self):
        frames = make_milestone_frames(5000)
        self.assertEqual(len(frames), 8)
        self.assertTrue(all(len(frame) == 8 * 24 * 3 for frame in frames))
        self.assertEqual(len(set(frames)), 8)
        self.assertEqual(render_text("50K")[2], 0)  # blank rows stay dark

    def test_text_slot_writes_pad_with_nul_and_split(self):
        self.assertEqual(encode_slot("HI"), b"HI" + b"\0" * 28)
        self.assertEqual(len(encode_slot("")), 30)
        parts = segments("A" * 30)
        self.assertEqual([off for off, _ in parts], [0, 25])
        self.assertEqual([len(chunk) for _, chunk in parts], [25, 5])
        self.assertEqual(segments("hi")[1][1], b"\0" * 5)  # tail always cleared

    def test_text_slot_read_joins_segments(self):
        self.assertEqual(read_slot(0, 1, one_packet_read), "HELLO")

    def test_text_slot_read_rejected(self):
        self.assertIsNone(read_slot(0, 1, lambda *a, **k: bytes([0xFF])))

    def test_text_slot_write_issues_two_reports(self):
        calls = []

        def hid(device, payload, **kwargs):
            calls.append(payload)
            return bytes(payload[:4])

        self.assertTrue(write_slot(0, 3, "x" * 40, hid))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:6], [0x07, 26, 5, 3, 0, 25])
        self.assertEqual(calls[1][:6], [0x07, 26, 5, 3, 25, 5])

    def test_text_slot_write_rejected(self):
        def hid(device, payload, **kwargs):
            return bytes([0xFF])
        self.assertFalse(write_slot(0, 0, "hi", hid))

    def test_preset_library_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            frames = [bytes((index % 256) for index in range(576))]
            presetlib.save("测试/A:预设", frames, 7, "ai", directory)
            loaded, fps = presetlib.load("测试/A:预设", directory)
            self.assertEqual(fps, 7)
            self.assertEqual(loaded, frames)
            self.assertEqual(presetlib.names(directory), ["测试/A:预设"])  # display name kept
            presetlib.rename("测试_A:预设", "second", directory)
            self.assertEqual(presetlib.names(directory), ["second"])
            exported = Path(folder) / "out" / "outside.json"  # outside the library folder
            exported.parent.mkdir(exist_ok=True)
            presetlib.export_to("second", exported, directory)
            imported = presetlib.import_from(exported, directory)
            self.assertEqual(imported, "second")
            presetlib.delete("second", directory)
            self.assertEqual(presetlib.names(directory), [])

    def test_scheduler_ranges_including_midnight_wrap(self):
        import datetime as dt
        rule = {"kind": "mode", "value": 1, "start": "09:00", "end": "18:00"}
        self.assertTrue(scheduler.matches(rule, dt.time(9, 0)))
        self.assertTrue(scheduler.matches(rule, dt.time(17, 59)))
        self.assertFalse(scheduler.matches(rule, dt.time(18, 0)))
        night = {"kind": "brightness", "value": 90, "start": "22:00", "end": "07:00"}
        self.assertTrue(scheduler.matches(night, dt.time(23, 30)))
        self.assertTrue(scheduler.matches(night, dt.time(2, 0)))
        self.assertFalse(scheduler.matches(night, dt.time(12, 0)))
        self.assertIsNone(scheduler.active_rule({"enabled": False, "rules": [rule]}, dt.datetime(2026, 1, 1, 10)))
        config = {"enabled": True, "rules": [night, rule]}
        self.assertEqual(scheduler.active_rule(config, dt.datetime(2026, 1, 1, 10))["value"], 1)
        self.assertEqual(scheduler.active_rule(config, dt.datetime(2026, 1, 1, 23, 5))["value"], 90)

    def test_scheduler_rejects_bad_rules_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "schedule.json"
            path.write_text(json.dumps({"enabled": True, "rules": [
                {"kind": "mode", "value": 4, "start": "09:00", "end": "10:00"},
                {"kind": "nonsense", "value": 1},
            ]}), encoding="utf-8")
            config = scheduler.load(path)
            self.assertEqual(len(config["rules"]), 1)
            self.assertEqual(config["rules"][0]["value"], 4)

    def test_milestones_fire_once_per_threshold(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "milestone.json"
            events = Milestones(path, step=1000)
            self.assertIsNone(events.sync(900))
            self.assertEqual(events.sync(1000), 1000)
            self.assertIsNone(events.sync(1500))
            # a polling gap that crosses new multiples fires once per sync
            self.assertIsNone(events.sync(1999))
            self.assertEqual(events.sync(2500), 2500)   # 2000 crossed since last sync
            self.assertEqual(events.sync(5000), 5000)
            self.assertEqual(events.remaining(5000), 1000)
            self.assertIsNone(events.sync(5999))
            # restart mid-day: already-fired multiples never repeat,
            # but a fresh crossing still fires (even if late)
            self.assertIsNone(Milestones(path, step=1000).sync(5000))
            self.assertIsNone(Milestones(path, step=1000).sync(5999))
            self.assertEqual(Milestones(path, step=1000).sync(7000), 7000)

    def test_milestone_settings_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "milestone.json"
            events = Milestones(path, step=1000)
            events.configure(10000, False)
            reloaded = Milestones(path, step=1000)
            self.assertEqual(reloaded.step, 10000)
            self.assertFalse(reloaded.enabled)
            self.assertEqual(reloaded.remaining(3000), 7000)

    def test_ai_design_parses_grid_frames_and_clamps(self):
        design = json.dumps({
            "fps": 4,
            "palette": {"A": "#FF3366", "B": "#101010", "C": "#000000"},
            "frames": [
                ["AAAAAAAAAAAAAAAAAAAAAAAA"] * 7 + ["." * 24],
                ["ABABABABABABABABABABABAB"] + ["." * 24] * 7,
            ],
        })
        fps, frames = ai_designer.parse_design(design)
        self.assertEqual(fps, 4)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(frame) == 8 * 24 * 3 for frame in frames))
        frame = frames[0]
        hue, sat, value = frame[3:6]
        self.assertGreaterEqual(sat, 90)   # near-grey clamped away from red bug
        self.assertGreaterEqual(value, 26)  # near-black lifted
        self.assertEqual(frames[0][(7 * 24) * 3:(7 * 24) * 3 + 3], b"\0\0\0")

    def test_ai_designer_rejects_bad_shapes(self):
        with self.assertRaises(ValueError):
            ai_designer.parse_design('{"fps": 4, "palette": {}, "frames": [["x" * 24] * 8]}')
        with self.assertRaises(ValueError):
            ai_designer.parse_design("no json here")
        with self.assertRaises(ValueError):
            ai_designer.parse_design(json.dumps({"fps": 4, "palette": {"a": "#ff0000"},
                                                 "frames": [["A" * 24] * 8]}))

    def test_light_effect_tables_and_color_args(self):
        self.assertEqual(len(lights.BAR_EFFECTS), 43)
        self.assertEqual(len(lights.KEY_EFFECTS), 45)
        options = lights.effect_options("bar")
        self.assertTrue(options[0].startswith("0 ·"))
        self.assertEqual(lights.effect_index("bar", options[15]), 15)
        hue, sat = lights.color_command_args("#ff0000")
        self.assertEqual((hue, sat), (0, 255))
        hue, sat = lights.color_command_args("#00ff00")
        self.assertLess(abs(hue - 85), 6)

    def test_keyboard_and_serial_are_matched_by_identity(self):
        descriptor = {"interface_number": 1, "usage_page": 0xFF60,
                      "serial_number": "F576C7BF C93C817", "path": b"device"}
        ports = [SimpleNamespace(vid=0xE560, pid=0xE104, serial_number="OTHER", device="COM4"),
                 SimpleNamespace(vid=0xE560, pid=0xE104,
                                 serial_number="F576C7BF_C93C817", device="COM5")]
        with patch("studio.device.hid.enumerate", return_value=[descriptor]), \
             patch("studio.device.list_ports.comports", return_value=ports):
            self.assertIs(find_keyboard(), descriptor)
            self.assertEqual(find_port(descriptor), "COM5")

    def test_hid_command_ignores_stale_matrix_reply(self):
        class FakeHID:
            def __init__(self):
                self.replies = [[2, 3, 0, 0], [8, 26, 2, 4]]

            def write(self, data):
                self.sent = data

            def read(self, length):
                return self.replies.pop(0) if self.replies else []

        fake = FakeHID()
        self.assertEqual(hid_command(fake, [8, 26, 2], timeout=.1), bytes([8, 26, 2, 4]))
        self.assertEqual(fake.sent[:4], [0, 8, 26, 2])

    def test_failed_animation_aborts_transfer(self):
        class FakeSerial:
            def __init__(self, *args, **kwargs):
                self.writes = []
                header = bytearray(64)
                header[0], header[6] = 0xC0, 1
                bad_ack = bytearray(64)
                bad_ack[0], bad_ack[5] = 0xC1, 0xEE
                self.replies = [bytes(header), bytes(bad_ack)]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def reset_input_buffer(self):
                pass

            def reset_output_buffer(self):
                pass

            def write(self, packet):
                self.writes.append(packet)

            def read(self, length):
                return self.replies.pop(0) if self.replies else b""

        fake = FakeSerial()
        with patch("studio.device.serial.Serial", return_value=fake):
            with self.assertRaises(RuntimeError):
                upload_animation("COM5", [bytes(576)])
        self.assertEqual([packet[0] for packet in fake.writes], [0xC0, 0xC1, 0xE2])

    def test_custom_mode_is_selected_before_upload_and_not_reselected_after(self):
        events = []
        modes = iter((bytes([8, 26, 2, 3]), bytes([8, 26, 2, 2])))

        def command(_device, payload, **_kwargs):
            events.append(tuple(payload))
            return next(modes) if payload[0] == 8 else bytes([7, 26, 2, 2])

        def upload(_port, _frames, _fps):
            events.append("upload")
            return {"frames": 1, "chunks": 11, "acknowledged": True}

        with patch("studio.device.hid_command", side_effect=command), \
             patch("studio.device.upload_animation", side_effect=upload), \
             patch("studio.device.time.sleep"):
            send_animation(object(), "COM5", [bytes(576)])
        self.assertEqual(events, [(8, 26, 2), (7, 26, 2, 2), "upload", (8, 26, 2)])

    def test_existing_custom_mode_stays_selected_during_upload(self):
        events = []

        def command(_device, payload, **_kwargs):
            events.append(tuple(payload))
            return bytes([8, 26, 2, 2])

        def upload(_port, _frames, _fps):
            events.append("upload")
            return {"frames": 1, "chunks": 11, "acknowledged": True}

        with patch("studio.device.hid_command", side_effect=command), \
             patch("studio.device.upload_animation", side_effect=upload):
            send_animation(object(), "COM5", [bytes(576)])
        self.assertEqual(events, [(8, 26, 2), "upload", (8, 26, 2)])


def one_packet_read(device, payload, **kwargs):
    data = b"HELLO" + b"\0" * 25
    if payload[0] == 0x08 and payload[4] == 0:
        return bytes([0x08, 26, 5, payload[3], 0, 25]) + data[:25]
    if payload[0] == 0x08 and payload[4] == 25:
        return bytes([0x08, 26, 5, payload[3], 25, 5]) + data[:5]
    return bytes([0xFF])


if __name__ == "__main__":
    unittest.main()
