"""Generate the PWA icons from the MaintainIQ brand mark.

design/2026-10-07-mobile-operator-pwa-design.md, decision 19. The brand mark is
lucide's ``Radar`` glyph, white, on an azure -> violet diagonal gradient tile
(``--color-accent-2 #4dc9ff`` -> ``--color-accent #7c6cff``; see
``frontend/src/layout/AppShell.tsx`` and ``frontend/src/index.css``).

Dev-only: needs Pillow, which is deliberately *not* in requirements.txt. The
PNGs it writes are committed, so nobody has to run this unless the mark
changes::

    python scripts/generate_pwa_icons.py

Writes to ``frontend/public/icons/``:

- ``icon-192.png``, ``icon-512.png``: rounded gradient tile on black (purpose "any")
- ``apple-touch-icon-180.png``: full-bleed, opaque (iOS applies its own mask)
- ``icon-maskable-512.png``: full-bleed, glyph inside the 80 % safe zone
- ``badge-96.png``: white glyph on transparent (Android status bar)

Everything is drawn at 4x and downsampled, for clean anti-aliased edges.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parents[1] / "frontend" / "public" / "icons"
SUPERSAMPLE = 4
AZURE = (0x4D, 0xC9, 0xFF)
VIOLET = (0x7C, 0x6C, 0xFF)
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)

# lucide "radar" on its 24-unit grid: arcs as (radius, start, end) in Pillow's
# degrees (clockwise from 3 o'clock, y down), converted from the SVG paths.
RADAR_ARCS = [
    (10, -120.0, -45.0),  # M19.07 4.93 A10 10 0 0 0 6.99 3.34
    (10, -21.4, 193.8),  # M2.29 9.62 A10 10 0 1 0 21.31 8.35
    (6, 128.9, 315.0),  # M16.24 7.76 A6 6 0 1 0 8.23 16.67
    (6, -3.2, 51.1),  # M17.99 11.66 A6 6 0 0 1 15.77 16.67
    (2, 0.0, 360.0),  # circle r=2
]
RADAR_DOTS = [(4.0, 6.0), (12.0, 18.0)]
RADAR_SWEEP = ((13.41, 10.59), (19.07, 4.93))
STROKE = 2.0  # lucide's stroke width, in grid units


def gradient(size: int) -> Image.Image:
    """A diagonal azure (top-left) -> violet (bottom-right) square."""
    img = Image.new("RGBA", (size, size))
    px = img.load()
    span = 2 * (size - 1) or 1
    for y in range(size):
        for x in range(size):
            t = (x + y) / span
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(AZURE, VIOLET)) + (255,)
    return img


def draw_glyph(img: Image.Image, box: tuple[float, float, float], colour=WHITE) -> None:
    """Draw the radar glyph into ``box`` = (left, top, side) of ``img``."""
    left, top, side = box
    unit = side / 24.0
    width = max(1, round(STROKE * unit))
    draw = ImageDraw.Draw(img)

    def pt(x: float, y: float) -> tuple[float, float]:
        return left + x * unit, top + y * unit

    def cap(x: float, y: float) -> None:
        cx, cy = pt(x, y)
        r = width / 2
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour)

    # Pillow strokes an arc *inside* its bounding box, so grow the box by half
    # the stroke to centre the stroke on the radius, as SVG does.
    half = STROKE / 2
    for radius, start, end in RADAR_ARCS:
        x0, y0 = pt(12 - radius - half, 12 - radius - half)
        x1, y1 = pt(12 + radius + half, 12 + radius + half)
        draw.arc((x0, y0, x1, y1), start, end, fill=colour, width=width)
        if end - start < 360:
            for angle in (start, end):
                a = math.radians(angle)
                cap(12 + radius * math.cos(a), 12 + radius * math.sin(a))
    for x, y in RADAR_DOTS:
        cap(x, y)
    (ax, ay), (bx, by) = RADAR_SWEEP
    draw.line((pt(ax, ay), pt(bx, by)), fill=colour, width=width)
    cap(ax, ay)
    cap(bx, by)


def rounded_tile(size: int) -> Image.Image:
    """Purpose "any": a rounded gradient tile, glyph at 60 %, on black."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), BLACK)
    inset = round(big * 0.04)
    tile = gradient(big - 2 * inset)
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile.size[0] - 1, tile.size[1] - 1), radius=round(big * 0.22), fill=255)
    img.paste(tile, (inset, inset), mask)
    glyph = big * 0.6
    draw_glyph(img, ((big - glyph) / 2, (big - glyph) / 2, glyph))
    return img.resize((size, size), Image.LANCZOS)


def full_bleed(size: int, glyph_share: float) -> Image.Image:
    """Edge-to-edge gradient (maskable / Apple), glyph at ``glyph_share``."""
    big = size * SUPERSAMPLE
    img = gradient(big)
    glyph = big * glyph_share
    draw_glyph(img, ((big - glyph) / 2, (big - glyph) / 2, glyph))
    return img.resize((size, size), Image.LANCZOS)


def badge(size: int) -> Image.Image:
    """Monochrome white glyph on transparent, for the status bar."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    glyph = big * 0.84
    draw_glyph(img, ((big - glyph) / 2, (big - glyph) / 2, glyph))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "icon-192.png": rounded_tile(192),
        "icon-512.png": rounded_tile(512),
        # The maskable safe zone is the central 80 % circle; 50 % keeps the
        # glyph's corners well inside it.
        "icon-maskable-512.png": full_bleed(512, 0.5),
        "apple-touch-icon-180.png": full_bleed(180, 0.58).convert("RGB"),
        "badge-96.png": badge(96),
    }
    for name, image in outputs.items():
        image.save(OUT_DIR / name, optimize=True)
        print(f"wrote {OUT_DIR / name} ({image.size[0]}x{image.size[1]})")


if __name__ == "__main__":
    main()
