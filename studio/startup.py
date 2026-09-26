"""Current-user Windows logon startup for the DP104 tray console."""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DP104Studio"


def command_for_app(app_path: Path | None = None) -> str:
    """Use the packaged exe, or pythonw plus app.pyw for source runs."""
    if getattr(sys, "frozen", False):
        executable = Path(app_path or sys.executable).resolve()
        return f'"{executable}" --tray'
    script = Path(app_path or Path(__file__).resolve().parents[1] / "app.pyw").resolve()
    runner = Path(sys.executable).with_name("pythonw.exe")
    if not runner.exists():
        runner = Path(sys.executable)
    return f'"{runner.resolve()}" "{script}" --tray'


def is_enabled(command: str | None = None) -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    return str(value).casefold() == (command or command_for_app()).casefold()


def set_enabled(enabled: bool, command: str | None = None) -> None:
    if os.name != "nt":
        raise OSError("开机启动仅支持 Windows")
    import winreg
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ,
                              command or command_for_app())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
