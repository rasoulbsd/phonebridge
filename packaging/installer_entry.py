"""Phonebridge-Setup.exe: installs the bundled app for the current user and starts it."""

import subprocess
import sys
import zipfile
from pathlib import Path

from phonebridge import __version__
from phonebridge.shell import install, registry


def main() -> None:
    payload = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "Phonebridge.zip"
    # --silent: update in place without dialogs (used for upgrades).
    silent = "--silent" in sys.argv
    if not silent and not install.message_box(
        f"Install {registry.DISPLAY_NAME} for Windows ({__version__}) for your Windows account?\n\n"
        "It adds it to the Start menu and desktop and starts it with Windows "
        "(you can turn that off from the tray icon).",
        yes_no=True,
    ):
        return
    try:
        exe = install.install(payload, __version__)
    except (OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        install.message_box(f"Installation failed: {exc}")
        return
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    if silent:
        return
    install.message_box(
        f"{registry.DISPLAY_NAME} is installed and starting. You'll find it in the Start menu and the tray."
    )


main()
