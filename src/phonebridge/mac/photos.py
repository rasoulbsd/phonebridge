"""Contact photos arrive full size from the Mac; avatars only need a small copy."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

_SIZE = 160
_QUALITY = 85


def thumbnail(data: bytes) -> bytes:
    """A small JPEG of the photo, or the original bytes when it can't be decoded here (e.g. HEIC)."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((_SIZE, _SIZE))
            out = io.BytesIO()
            image.convert("RGB").save(out, format="JPEG", quality=_QUALITY)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return data
    return out.getvalue()
