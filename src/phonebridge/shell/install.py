"""Per-user install and uninstall for the packaged app. No admin rights needed.

Standard library only: the small setup program bundles this module without the
rest of the app.
"""

from __future__ import annotations

import contextlib
import ctypes
import http.client
import os
import shutil
import socket
import subprocess
import time
import zipfile
from pathlib import Path

from phonebridge.shell import registry

EXE_NAME = "Phonebridge.exe"
DEFAULT_PORT = 8765
_MB_OK = 0x0
_MB_YESNO = 0x4
_MB_ICONINFO = 0x40
_IDYES = 6
_STOP_WAIT = 10.0
_RENAME_RETRIES = 20


def install_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / registry.APP_NAME


def message_box(text: str, *, yes_no: bool = False) -> bool:
    flags = (_MB_YESNO if yes_no else _MB_OK) | _MB_ICONINFO
    return ctypes.windll.user32.MessageBoxW(None, text, registry.DISPLAY_NAME, flags) in (_IDYES, 1)


def _system_tool(*parts: str) -> str:
    return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", *parts))


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def stop_running_app(port: int = DEFAULT_PORT) -> None:
    if _port_open(port):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        with contextlib.suppress(OSError):
            connection.request("POST", "/api/desktop/quit", headers={"X-Requested-With": "phonebridge"})
            connection.getresponse().read()
        connection.close()
        deadline = time.monotonic() + _STOP_WAIT
        while _port_open(port) and time.monotonic() < deadline:
            time.sleep(0.3)
    # Anything left over (other than this process) gets closed hard.
    subprocess.run(
        [_system_tool("taskkill.exe"), "/F", "/IM", EXE_NAME, "/FI", f"PID ne {os.getpid()}"],
        capture_output=True,
        check=False,
    )


def _powershell(script: str) -> None:
    subprocess.run(
        [
            _system_tool("WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        check=True,
    )


_SHORTCUT_NAMES = (registry.DISPLAY_NAME, registry.APP_NAME)


# Windows only pops up notifications from an installed desktop app when its Start menu
# shortcut carries the app's ID (System.AppUserModel.ID); WScript.Shell can't set that.
_AUMID_HELPER = r"""
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPbPropertyStore {
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, out PbPropertyKey key);
    [PreserveSig] int GetValue(ref PbPropertyKey key, out PbPropVariant value);
    [PreserveSig] int SetValue(ref PbPropertyKey key, ref PbPropVariant value);
    [PreserveSig] int Commit();
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PbPropertyKey { public Guid FormatId; public uint PropertyId; }

[StructLayout(LayoutKind.Explicit, Size = 24)]
public struct PbPropVariant { [FieldOffset(0)] public ushort VarType; [FieldOffset(8)] public IntPtr Pointer; }

public static class PbAumid {
    public static void Set(string shortcut, string appId) {
        object link = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
        IPersistFile file = (IPersistFile)link;
        file.Load(shortcut, 2);
        IPbPropertyStore store = (IPbPropertyStore)link;
        PbPropertyKey key = new PbPropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
        PbPropVariant value = new PbPropVariant { VarType = 31, Pointer = Marshal.StringToCoTaskMemUni(appId) };
        try {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
            Marshal.ThrowExceptionForHR(store.Commit());
            file.Save(shortcut, true);
        } finally {
            Marshal.FreeCoTaskMem(value.Pointer);
            Marshal.ReleaseComObject(link);
        }
    }
}
"""


def _shortcut_script(exe: Path) -> str:
    target = str(exe).replace("'", "''")
    workdir = str(exe.parent).replace("'", "''")
    return (
        f"Add-Type -TypeDefinition @'\n{_AUMID_HELPER}\n'@\n"
        "$shell = New-Object -ComObject WScript.Shell;"
        "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {"
        # Earlier versions used the old name; drop those shortcuts.
        f" Remove-Item -LiteralPath (Join-Path $dir '{registry.APP_NAME}.lnk') -ErrorAction SilentlyContinue;"
        f" $path = Join-Path $dir '{registry.DISPLAY_NAME}.lnk';"
        " $link = $shell.CreateShortcut($path);"
        f" $link.TargetPath = '{target}'; $link.WorkingDirectory = '{workdir}';"
        f" $link.IconLocation = '{target},0'; $link.Description = 'Your iPhone messages on this PC';"
        " $link.Save();"
        f" try {{ [PbAumid]::Set($path, '{registry.APP_ID}') }} catch {{ Write-Warning $_ }} }}"
    )


def _remove_shortcuts_script() -> str:
    names = ", ".join(f"'{name}.lnk'" for name in _SHORTCUT_NAMES)
    return (
        "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {"
        f" foreach ($name in @({names})) {{"
        " Remove-Item -LiteralPath (Join-Path $dir $name) -ErrorAction SilentlyContinue } }"
    )


def _clear_dir(path: Path) -> None:
    if not path.exists():
        return
    # Files can stay locked for a moment after the old app exits.
    for _ in range(_RENAME_RETRIES):
        try:
            shutil.rmtree(path)
        except PermissionError:
            time.sleep(0.5)
        else:
            return
    shutil.rmtree(path)


def install(payload: Path, version: str) -> Path:
    """Unpacks the app for the current user and registers it. Returns the installed exe."""
    stop_running_app()
    target = install_dir()
    _clear_dir(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(payload) as archive:
        archive.extractall(target)
    exe = target / EXE_NAME
    _powershell(_shortcut_script(exe))
    registry.register_uninstaller(target, exe, version)
    registry.set_autostart(enabled=True, command=f'"{exe}" --background')
    return exe


def uninstall(data_dir: Path, *, port: int = DEFAULT_PORT) -> None:
    if not message_box(f"Remove {registry.DISPLAY_NAME} from this PC?", yes_no=True):
        return
    delete_history = message_box(
        "Also delete your saved message history?\n\nChoose No to keep it in case you reinstall.", yes_no=True
    )
    stop_running_app(port)
    with contextlib.suppress(subprocess.CalledProcessError):
        _powershell(_remove_shortcuts_script())
    registry.remove_all()
    if delete_history:
        shutil.rmtree(data_dir, ignore_errors=True)
    target = install_dir()
    if target.exists():
        # This exe lives in the folder being deleted, so a detached shell removes it after we exit.
        subprocess.Popen(
            [_system_tool("cmd.exe"), "/c", f'ping -n 3 127.0.0.1 >nul & rmdir /s /q "{target}"'],
            cwd=str(target.parent),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
    message_box(f"{registry.DISPLAY_NAME} was removed.")
