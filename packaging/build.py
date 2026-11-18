"""Builds the Windows installer. Run with the project venv's python on Windows.

    python packaging/build.py             public build: dist/Phonebridge-Setup-<version>.exe
    python packaging/build.py --personal  adds the gitignored personal branding:
                                          dist/Phonebridge-Setup-<version>-personal.exe

Public builds are checked to contain no personal branding before they're accepted.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
LOCAL_BRANDING = "phonebridge.branding_local"


def pyinstaller(spec: str, distpath: Path, env: dict[str, str]) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            "--distpath",
            str(distpath),
            "--workpath",
            str(BUILD / "work"),
            str(ROOT / "packaging" / spec),
        ],
        check=True,
        env=env,
    )


def bundled_modules(executable: Path) -> set[str]:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    archive = CArchiveReader(str(executable))
    pyz_name = next(name for name in archive.toc if name.endswith(".pyz"))
    with tempfile.TemporaryDirectory() as scratch:
        pyz_path = Path(scratch) / "modules.pyz"
        pyz_path.write_bytes(archive.extract(pyz_name))
        return set(ZlibArchiveReader(str(pyz_path)).toc)


def check_public(app_dir: Path) -> None:
    personal_files = [p for p in app_dir.rglob("*") if "-local." in p.name]
    if personal_files:
        sys.exit(f"public build contains personal files: {personal_files}")
    if LOCAL_BRANDING in bundled_modules(app_dir / "Phonebridge.exe"):
        sys.exit("public build contains the personal branding module")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--personal", action="store_true", help="include the gitignored personal branding")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from phonebridge import __version__

    env = {
        **os.environ,
        "PHONEBRIDGE_VERSION": __version__,
        "PHONEBRIDGE_PERSONAL_BUILD": "1" if args.personal else "0",
    }
    app_dist = BUILD / "app"
    shutil.rmtree(app_dist, ignore_errors=True)
    pyinstaller("phonebridge.spec", app_dist, env)

    app_dir = app_dist / "Phonebridge"
    if not args.personal:
        check_public(app_dir)
    payload = BUILD / "Phonebridge.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(app_dir))

    DIST.mkdir(exist_ok=True)
    pyinstaller("installer.spec", DIST, env)
    setup = DIST / f"Phonebridge-Setup-{__version__}{'-personal' if args.personal else ''}.exe"
    print(f"built {setup} ({setup.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
