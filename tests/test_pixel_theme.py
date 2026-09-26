"""Interaction checks for the pixel dropdown's dismissal and hidden-page guard."""

import os
import runpy
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.pixel_theme import PixelDropdown
from studio import startup


@unittest.skipUnless(os.name == "nt", "Tk desktop interaction requires Windows")
class PixelDropdownTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("320x220+100+100")
        self.page = tk.Frame(self.root)
        self.page.pack(fill="both", expand=True)
        self.dropdown = PixelDropdown(self.page, ["First", "Second"], width=15)
        self.dropdown.pack()
        self.outside = tk.Button(self.page, text="Outside")
        self.outside.pack()
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_clicking_elsewhere_dismisses_without_selection(self):
        self.dropdown._open()
        self.root.update()
        self.assertIsNotNone(self.dropdown.popup)
        self.assertIsNone(self.root.grab_current())
        self.outside.event_generate("<ButtonPress-1>")
        self.root.update()
        self.assertIsNone(self.dropdown.popup)
        self.assertEqual(self.dropdown.get(), "First")

    def test_hidden_page_cannot_open_with_down_key(self):
        self.page.pack_forget()
        self.root.update()
        self.dropdown._open()
        self.assertIsNone(self.dropdown.popup)

    def test_home_page_arrow_cannot_reopen_lights_menu(self):
        Studio = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.pyw"))["Studio"]
        with patch.object(Studio, "_start_tray"), \
             patch.object(Studio.__init__.__globals__["DeviceWorker"], "start"), \
             patch.object(Studio.__init__.__globals__["AutoWorker"], "start"):
            app = Studio(None)
        try:
            app.change_preset_tab("lights")
            app.root.update()
            dropdown = app.light_widgets["keys"]["effect"]
            dropdown.trigger.focus_set()
            app.change_page("home")
            dropdown.trigger.event_generate("<Down>")
            app.root.update()
            self.assertIsNone(dropdown.popup)
            self.assertEqual(app.page, "home")
        finally:
            for handle in app.root.tk.call("after", "info"):
                app.root.after_cancel(handle)
            app.root.destroy()

    def test_tray_startup_starts_withdrawn(self):
        Studio = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.pyw"))["Studio"]
        with patch("sys.argv", ["app.pyw", "--tray"]), \
             patch.object(Studio, "_start_tray"), \
             patch.object(Studio.__init__.__globals__["DeviceWorker"], "start"), \
             patch.object(Studio.__init__.__globals__["AutoWorker"], "start"):
            app = Studio(None)
        try:
            self.assertEqual(app.root.state(), "withdrawn")
            self.assertTrue(app.start_in_tray)
        finally:
            for handle in app.root.tk.call("after", "info"):
                app.root.after_cancel(handle)
            app.root.destroy()

    def test_packaged_startup_command_uses_current_exe(self):
        path = Path("C:/Program Files/DP104/DP104-Studio.exe")
        with patch.object(startup.sys, "frozen", True, create=True):
            command = startup.command_for_app(path)
        self.assertEqual(command, f'"{path.resolve()}" --tray')


if __name__ == "__main__":
    unittest.main()
