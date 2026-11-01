"""Wire-format regressions for the vendor's HID animation fallback."""
import queue
import unittest
from unittest.mock import patch

from studio.device import (DeviceWorker, TransferError, get_cdc_support,
                           hid_command, select_screen_mode, upload_animation_hid)


class EchoHID:
    def __init__(self, cdc=False, reject_header=False, reject_chunk=None):
        self.sent = []
        self.pending = []
        self.cdc = cdc
        self.reject_header = reject_header
        self.reject_chunk = reject_chunk

    def write(self, packet):
        self.sent.append(bytes(packet))
        payload = bytearray(packet[1:])
        if payload[0] == 0xBF:
            payload[1] = int(self.cdc)
        if payload[:2] == bytes([0xD1, 0x30]) and self.reject_header:
            payload[-1] = 7
        if payload[:2] == bytes([0xD1, 0x31]) and self.reject_chunk == int.from_bytes(payload[2:6], "big"):
            payload = bytearray([0xFF] + [0] * 31)
        self.pending.append(list(payload))
        return len(packet)

    def read(self, size):
        return self.pending.pop(0) if self.pending else []


class HidTransportTests(unittest.TestCase):
    def test_capability_false_true_and_unsupported(self):
        self.assertIs(get_cdc_support(EchoHID(cdc=False)), False)
        self.assertIs(get_cdc_support(EchoHID(cdc=True)), True)
        with patch("studio.device.hid_command", return_value=bytes([0xFF, 0])):
            self.assertIsNone(get_cdc_support(object()))

    def test_full_rgb_hsv_stream_crosses_frame_boundaries_without_truncation(self):
        frames = [bytes((i * 17 + n) % 256 for i in range(576)) for n in (3, 91)]
        h = EchoHID()
        receipt = upload_animation_hid(h, frames, 12)
        self.assertEqual(receipt["frames"], 2)
        self.assertEqual(receipt["chunks"], 47)
        self.assertEqual(h.sent[0][:7], bytes([0, 0xD1, 0x30, 2, 12, 8, 24]))
        combined = bytearray()
        for packet in h.sent[1:]:
            self.assertEqual(len(packet), 33)
            self.assertEqual(packet[1:3], bytes([0xD1, 0x31]))
            self.assertEqual(int.from_bytes(packet[3:7], "big"), len(combined))
            self.assertLessEqual(packet[7], 25)
            combined.extend(packet[8:8 + packet[7]])
        self.assertEqual(bytes(combined), b"".join(frames))
        self.assertTrue(receipt["acknowledged"])

    def test_header_error_stops_before_sending_pixels(self):
        h = EchoHID(reject_header=True)
        with self.assertRaisesRegex(TransferError, "错误码 7"):
            upload_animation_hid(h, [bytes(576)])
        self.assertEqual(len(h.sent), 1)

    def test_failed_chunk_stops_transfer(self):
        h = EchoHID(reject_chunk=25)
        with self.assertRaisesRegex(TransferError, "25"):
            upload_animation_hid(h, [bytes(576)])
        self.assertEqual(len(h.sent), 3)

    def test_short_or_missing_ack_is_not_success(self):
        for reply in (None, bytes([0xD1, 0x30, 1, 8, 8, 24])):
            with self.subTest(reply=reply), patch("studio.device.hid_command", return_value=reply):
                with self.assertRaises(TransferError):
                    upload_animation_hid(object(), [bytes(576)])

    def test_invalid_animation_sends_nothing(self):
        for frames, fps in [([], 8), ([bytes(575)], 8), ([bytes(576)], 31), ([bytes(576)] * 201, 8)]:
            h = EchoHID()
            with self.assertRaises(ValueError):
                upload_animation_hid(h, frames, fps)
            self.assertEqual(h.sent, [])

    def test_hid_selection_never_requires_or_probes_a_serial_port(self):
        worker = DeviceWorker(None, queue.Queue())
        with patch("studio.device.find_port") as ports, patch("studio.device.probe_serial") as probe:
            state = worker._serial_state({}, EchoHID(cdc=False))
        self.assertEqual(state["transport"], "hid")
        self.assertTrue(state["upload_ready"])
        self.assertFalse(state["serial"])
        self.assertFalse(worker.serial_failed)
        ports.assert_not_called()
        probe.assert_not_called()

    def test_legacy_cdc_still_matches_and_probes_port(self):
        worker = DeviceWorker(None, queue.Queue())
        with patch("studio.device.find_port", return_value="COM9"), patch("studio.device.probe_serial") as probe:
            state = worker._serial_state({}, EchoHID(cdc=True))
        self.assertEqual(state["transport"], "cdc")
        self.assertTrue(state["serial"])
        self.assertTrue(state["upload_ready"])
        probe.assert_called_once_with("COM9")

    def test_oversized_hid_payload_is_rejected_instead_of_silently_truncated(self):
        h = EchoHID()
        with self.assertRaises(ValueError):
            hid_command(h, [0xD1] * 33)
        self.assertEqual(h.sent, [])

    def test_selecting_current_diy_does_not_reload_or_save_it(self):
        with patch("studio.device.hid_command", return_value=bytes([8, 26, 2, 2])) as cmd:
            select_screen_mode(object(), 2)
        self.assertEqual([call.args[1] for call in cmd.call_args_list], [[8, 26, 2]])

    def test_switching_to_diy_saves_and_verifies_mode(self):
        replies = [bytes([8, 26, 2, 6]), bytes([7, 26, 2, 2]),
                   bytes([9, 26]), bytes([8, 26, 2, 2])]
        with patch("studio.device.hid_command", side_effect=replies) as cmd:
            select_screen_mode(object(), 2)
        self.assertEqual([call.args[1] for call in cmd.call_args_list],
                         [[8, 26, 2], [7, 26, 2, 2], [9, 26], [8, 26, 2]])

    def test_mode_mismatch_after_save_is_not_success(self):
        replies = [bytes([8, 26, 2, 6]), bytes([7, 26, 2, 2]),
                   bytes([9, 26]), bytes([8, 26, 2, 6])]
        with patch("studio.device.hid_command", side_effect=replies):
            with self.assertRaisesRegex(RuntimeError, "回读不一致"):
                select_screen_mode(object(), 2)


if __name__ == "__main__":
    unittest.main()
