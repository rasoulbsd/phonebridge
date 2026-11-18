"""Draws Phonebridge's own icon (blue rounded square, white chat bubble with dots) as .ico and .png."""

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "src" / "phonebridge" / "assets"
BLUE = (10, 124, 255, 255)
WHITE = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=BLUE)
    left, top, right, bottom = s * 0.2, s * 0.24, s * 0.8, s * 0.68
    d.rounded_rectangle((left, top, right, bottom), radius=int(s * 0.16), fill=WHITE)
    d.polygon([(s * 0.3, bottom - s * 0.04), (s * 0.27, s * 0.8), (s * 0.44, bottom - s * 0.02)], fill=WHITE)
    for x in (0.38, 0.5, 0.62):
        r = s * 0.04
        cx, cy = s * x, (top + bottom) / 2
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BLUE)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    big = draw(256)
    big.save(ASSETS / "phonebridge.png")
    big.save(
        ASSETS / "phonebridge.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("wrote", ASSETS)


if __name__ == "__main__":
    main()
