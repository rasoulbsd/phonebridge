# PyInstaller spec for the desktop app (a folder with Phonebridge.exe). Built by packaging/build.py.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# Public (release) builds never include the gitignored personal branding.
PERSONAL = os.environ.get("PHONEBRIDGE_PERSONAL_BUILD") == "1"
SRC = os.path.join(ROOT, "src")
PKG = os.path.join(SRC, "phonebridge")

LOCAL_BRANDING = "phonebridge.branding_local"
ASSETS = os.path.join(PKG, "assets")
ICON = os.path.join(ASSETS, "phonebridge-local.ico" if PERSONAL else "phonebridge.ico")
# Personal icons (*-local.*) ship only in personal builds.
asset_files = [
    (os.path.join(ASSETS, name), os.path.join("phonebridge", "assets"))
    for name in os.listdir(ASSETS)
    if PERSONAL or "-local." not in name
]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("winrt")
    + [m for m in collect_submodules("phonebridge") if PERSONAL or m != LOCAL_BRANDING]
    + ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]
)

a = Analysis(
    [os.path.join(ROOT, "packaging", "app_entry.py")],
    pathex=[SRC],
    datas=[
        (os.path.join(PKG, "web", "static"), os.path.join("phonebridge", "web", "static")),
        *asset_files,
        *collect_data_files("webview"),
    ],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff", "ty", *([] if PERSONAL else [LOCAL_BRANDING])],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Phonebridge",
    icon=ICON,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Phonebridge", upx=False)
