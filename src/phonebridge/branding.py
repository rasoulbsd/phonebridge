"""The name and icon the app shows.

Public builds are "Phonebridge" with its own icon. A personal build can override them
with a `branding_local.py` next to this file (gitignored, never published or released):
it may set DISPLAY_NAME, ICON_PNG and ICON_ICO (file names inside assets/).
Standard library only, so the small installer can use it.
"""

from __future__ import annotations

import importlib

DISPLAY_NAME = "Phonebridge"
ICON_PNG = "phonebridge.png"
ICON_ICO = "phonebridge.ico"

try:
    _local = importlib.import_module("phonebridge.branding_local")
except ModuleNotFoundError:
    pass
else:
    DISPLAY_NAME = str(getattr(_local, "DISPLAY_NAME", DISPLAY_NAME))
    ICON_PNG = str(getattr(_local, "ICON_PNG", ICON_PNG))
    ICON_ICO = str(getattr(_local, "ICON_ICO", ICON_ICO))
