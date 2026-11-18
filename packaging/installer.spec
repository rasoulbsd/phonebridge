# PyInstaller spec for Phonebridge-Setup.exe (one file, carries the app as a zip). Built by packaging/build.py.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
PKG = os.path.join(SRC, "phonebridge")
VERSION = os.environ["PHONEBRIDGE_VERSION"]
# Public (release) builds never include the gitignored personal branding.
PERSONAL = os.environ.get("PHONEBRIDGE_PERSONAL_BUILD") == "1"
LOCAL_BRANDING = "phonebridge.branding_local"

a = Analysis(
    [os.path.join(ROOT, "packaging", "installer_entry.py")],
    pathex=[SRC],
    datas=[(os.path.join(ROOT, "build", "Phonebridge.zip"), ".")],
    # branding.py loads the personal module dynamically, so name it explicitly when wanted.
    hiddenimports=[LOCAL_BRANDING] if PERSONAL else [],
    excludes=[
        "tkinter",
        "phonebridge.web",
        "phonebridge.services",
        "pydantic",
        "fastapi",
        *([] if PERSONAL else [LOCAL_BRANDING]),
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"Phonebridge-Setup-{VERSION}{'-personal' if PERSONAL else ''}",
    icon=os.path.join(PKG, "assets", "phonebridge-local.ico" if PERSONAL else "phonebridge.ico"),
    console=False,
    upx=False,
)
