"""Per-user Windows registry entries: start with Windows, notification identity, uninstall entry.

Standard library only, so the small installer can use it without the rest of the app.
"""

from __future__ import annotations

import contextlib
import sys
import winreg
from pathlib import Path

# What the user sees (window, tray, notifications, shortcuts). Paths and keys keep APP_NAME.
from phonebridge.branding import DISPLAY_NAME

APP_NAME = "Phonebridge"
APP_ID = "Phonebridge.Desktop"
PUBLISHER = "Phonebridge"

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
_AUMID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
_SETTINGS_KEY = rf"Software\{APP_NAME}"
_SETTINGS_VALUE = "Settings"


def launch_command(*args: str) -> str:
    """Command line that starts the desktop app, frozen or from a dev checkout."""
    if getattr(sys, "frozen", False):
        parts = [sys.executable, *args]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        parts = [str(pythonw if pythonw.exists() else sys.executable), "-m", "phonebridge.shell", *args]
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
    except FileNotFoundError:
        return False
    return True


def set_autostart(*, enabled: bool, command: str | None = None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            value = command or launch_command("--background")
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, APP_NAME)


def register_notifications(icon: Path) -> None:
    """Lets an unpackaged app show toasts under its own name and icon."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _AUMID_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))


def register_uninstaller(install_dir: Path, exe: Path, version: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY) as key:
        values: dict[str, str | int] = {
            "DisplayName": DISPLAY_NAME,
            "DisplayVersion": version,
            "Publisher": PUBLISHER,
            "DisplayIcon": str(exe),
            "InstallLocation": str(install_dir),
            "UninstallString": f'"{exe}" --uninstall',
            "NoModify": 1,
            "NoRepair": 1,
        }
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)


def remove_all() -> None:
    set_autostart(enabled=False)
    for path in (_UNINSTALL_KEY, _AUMID_KEY):
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


class RegistryConfigBackup:
    """Copy of the settings file (Mac password still DPAPI-encrypted) in the user's registry."""

    def read(self) -> str | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SETTINGS_KEY) as key:
                value, _ = winreg.QueryValueEx(key, _SETTINGS_VALUE)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def write(self, text: str) -> None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _SETTINGS_KEY) as key:
            winreg.SetValueEx(key, _SETTINGS_VALUE, 0, winreg.REG_SZ, text)


def remove_settings_backup() -> None:
    with contextlib.suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _SETTINGS_KEY)


def prefers_dark_theme() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except FileNotFoundError:
        return False
    return value == 0
