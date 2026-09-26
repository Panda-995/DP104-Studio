"""The media pipeline must preserve the keyboard's physical frame contract."""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio.image_import import (_sample_indices, _timed_indices, image_to_frame,
                                 import_images, import_tabml, rgb_hex_to_hsv_pixel)
from studio.pets import SCENES, make_scene
from studio.transitions import tween_frames


class CreativeTests(unittest.TestCase):
    def test_transparent_image_and_fit_modes(self):
        art = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        art.putpixel((1, 1), (0, 0, 0, 0))
        for mode in ("完整显示", "裁切填满", "拉伸填满"):
            frame = image_to_frame(art, mode)
            self.assertEqual(len(frame), 576)
            self.assertTrue(any(frame[offset + 2] for offset in range(0, 576, 3)))
        self.assertEqual(rgb_hex_to_hsv_pixel("#000000"), b"\0\0\0")

    def test_gif_keeps_animation_and_uses_original_loop_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounce.gif"
            images = [Image.new("RGB", (24, 8), (255, 0, 0)),
                      Image.new("RGB", (24, 8), (0, 0, 255))]
            images[0].save(path, save_all=True, append_images=images[1:],
                           duration=[100, 100], loop=0)
            frames, fps = import_images([path])
        self.assertEqual((len(frames), fps), (2, 10))
        self.assertNotEqual(frames[0], frames[1])
        self.assertTrue(all(len(frame) == 576 for frame in frames))

    def test_numbered_sequence_is_sorted_naturally_and_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for number, color in ((10, "blue"), (2, "green"), (1, "red")):
                path = Path(directory) / f"frame{number}.png"
                Image.new("RGB", (24, 8), color).save(path)
                paths.append(path)
            frames, fps = import_images(paths)
        self.assertEqual((len(frames), fps), (3, 8))
        self.assertEqual(frames[0], image_to_frame(Image.new("RGB", (24, 8), "red")))
        self.assertEqual(frames[1], image_to_frame(Image.new("RGB", (24, 8), "green")))
        self.assertEqual(len(_sample_indices(240)), 200)
        self.assertEqual((_sample_indices(240)[0], _sample_indices(240)[-1]), (0, 239))

    def test_gif_timing_repeats_long_held_frame(self):
        indices, fps = _timed_indices([100, 900])
        self.assertEqual((len(indices), fps), (10, 10))
        self.assertEqual(indices.count(0), 1)
        self.assertEqual(indices.count(1), 9)

    def test_official_tabml_header_and_52_rgb_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.tabml"
            rgb = bytes((255, 255, 0)) * (8 * 24)
            path.write_bytes(b"tabml" + bytes((52, 10, 8, 24)) + bytes(23) + rgb * 52)
            frames, fps = import_tabml(path)
            self.assertEqual((len(frames), fps), (52, 10))
            self.assertEqual(len(frames[0]), 576)
            self.assertNotEqual(frames[0], rgb)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "不完整"):
                import_tabml(path)
            path.write_bytes(b"tabml" + bytes((51, 10, 8, 24)) + bytes(23) + rgb * 52)
            with self.assertRaisesRegex(ValueError, "不一致"):
                import_tabml(path)

    def test_pet_scenes_are_real_looping_animations(self):
        for name, _, fps, count in SCENES:
            with self.subTest(name=name):
                frames, actual_fps = make_scene(name)
                self.assertEqual((len(frames), actual_fps), (count, fps))
                self.assertTrue(all(len(frame) == 576 for frame in frames))
                self.assertGreater(len(set(frames)), 2)
                self.assertTrue(any(any(frame[offset] for offset in range(2, 576, 3))
                                    for frame in frames))

    def test_tween_adds_intermediate_led_brightness(self):
        on = rgb_hex_to_hsv_pixel("#ff0000") + bytes(573)
        off = bytes(576)
        frames, fps = tween_frames([on, off], 8, 2)
        self.assertEqual((len(frames), fps), (4, 16))
        self.assertEqual(frames[0], on)
        self.assertEqual(frames[2], off)
        self.assertTrue(0 < frames[1][2] < on[2])
        self.assertTrue(0 < frames[3][2] < on[2])
        with self.assertRaisesRegex(ValueError, "帧率"):
            tween_frames([on, off], 16, 2)


if __name__ == "__main__":
    unittest.main()
