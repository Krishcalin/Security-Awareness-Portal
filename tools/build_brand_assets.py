"""Derive the brand assets from the supplied logo.

The PNG that was handed over is the master and stays in the repository
untouched. Everything the application actually serves is derived from it here,
so there is one place a redraw has to reach and no hand-cropped copy quietly
drifting from it.

Two things the master cannot be used for as it stands.

IT CARRIES ITS OWN MARGIN. The lockup occupies about a third of a 2400x1792
page of white. A layout cannot space something that brings its own whitespace —
the panel would appear to have a wandering left edge — so the content is
trimmed to its own bounds and the margin is put back by CSS.

IT IS OPAQUE WHITE. Fine on a light panel and a visible white sticker on a dark
one. The alpha version below recovers transparency from the white ground, which
works because the artwork was drawn on white: a pixel's distance from white is
its coverage, and dividing the colour back out un-does the blend the anti-
aliasing did. Applied to artwork on any other ground it would wash the colours
out, so it is not a general tool.

    python -m tools.build_brand_assets            # rewrite the derived assets
    python -m tools.build_brand_assets --check    # exit 1 if they are stale
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
MASTER = BRAND / "logo-master.png"

#: How far off the paper colour a pixel has to be before it counts as artwork.
#: Low enough to keep the soft edge of the shield, high enough to ignore the
#: compression noise in a flat white field.
INK_THRESHOLD = 12

#: Widths the application actually uses. The lockup is wide by construction;
#: the mark is the shield on its own, for a header and a tab icon.
LOCKUP_WIDTH = 1200
MARK_WIDTH = 512
#: The header shows the shield at about 28 CSS pixels. Serving the 512px one
#: there costs 320KB on every page view to draw something the size of a
#: thumbnail; this is that asset at a size a high-density screen can still use.
MARK_SMALL_WIDTH = 128
FAVICON = 64

DERIVED = {
    "logo-lockup.png": "the full lockup, on white",
    "logo-lockup-alpha.png": "the full lockup, transparent",
    "logo-mark.png": "the shield alone, transparent",
    "logo-mark-128.png": "the shield, header-sized",
    "favicon.png": "the shield, tab-sized",
}


def content_box(image: Image.Image) -> tuple[int, int, int, int]:
    """The artwork's own bounds, ignoring the page it was drawn on."""
    rgb = image.convert("RGB")
    paper = rgb.getpixel((0, 0))
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, paper))
    box = difference.convert("L").point(
        lambda value: 255 if value > INK_THRESHOLD else 0).getbbox()
    if box is None:
        raise SystemExit("the master is blank")
    return box


def mark_box(image: Image.Image, box: tuple[int, int, int, int]) -> tuple:
    """Just the shield.

    Found rather than measured: the widest run of empty columns inside the
    lockup is the gap between the shield and the rule that separates it from
    the words. A hard-coded x would be wrong the first time the lockup is set
    with different spacing.
    """
    rgb = image.convert("RGB")
    paper = rgb.getpixel((0, 0))
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, paper))
    mask = difference.convert("L").point(
        lambda value: 255 if value > INK_THRESHOLD else 0)
    pixels = mask.load()

    empty = []
    for x in range(box[0], box[2]):
        if not any(pixels[x, y] for y in range(box[1], box[3])):
            empty.append(x)

    runs, run = [], []
    for x in empty:
        if run and x == run[-1] + 1:
            run.append(x)
        else:
            if run:
                runs.append(run)
            run = [x]
    if run:
        runs.append(run)
    if not runs:
        return box
    widest = max(runs, key=len)
    return (box[0], box[1], widest[0], box[3])


#: Anything this far from white is artwork, not the fringe where the artwork
#: was blended into the page. Below it, the distance from white IS the coverage
#: and the colour has to be divided back out; at or above it the pixel is a
#: solid colour that happens to be light.
SOLID_AT = 48

#: Below this, a pixel is the compression noise in what was meant to be a flat
#: white field, not the edge of anything. Without this floor that noise becomes
#: thousands of barely-there near-white pixels, invisible on a white panel and
#: a visible halo of speckles around the shield on a dark one.
NOISE_FLOOR = 8


def with_alpha(image: Image.Image) -> Image.Image:
    """Recover transparency from a white ground.

    Only valid because this artwork was drawn on white.

    The distinction that matters is between the anti-aliased fringe and the
    artwork proper. Treating distance-from-white as coverage everywhere — the
    obvious version of this — leaves every solid colour very slightly
    translucent: the navy came out at 94%, which looks identical on a white
    panel and washes out against a dark one. So a pixel clearly off white is
    kept as it is and made fully opaque, and only the fringe is un-blended.
    """
    rgb = image.convert("RGB")
    out = Image.new("RGBA", rgb.size)
    source, target = rgb.load(), out.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = source[x, y]
            distance = 255 - min(r, g, b)
            if distance < NOISE_FLOOR:
                target[x, y] = (0, 0, 0, 0)
            elif distance >= SOLID_AT:
                target[x, y] = (r, g, b, 255)
            else:
                # In the fringe: coverage is the distance, and the colour is
                # what it must have been before being blended with white.
                alpha = round(distance * 255 / SOLID_AT)
                scale = 255 / distance
                target[x, y] = (
                    max(0, min(255, round(255 - (255 - r) * scale))),
                    max(0, min(255, round(255 - (255 - g) * scale))),
                    max(0, min(255, round(255 - (255 - b) * scale))),
                    alpha)
    return out


def _resized(image: Image.Image, width: int) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def build() -> dict[str, bytes]:
    with Image.open(MASTER) as master:
        master.load()
        box = content_box(master)
        lockup = master.crop(box)
        mark = master.crop(mark_box(master, box))

        return {
            "logo-lockup.png": _png(_resized(lockup.convert("RGB"),
                                             LOCKUP_WIDTH)),
            "logo-lockup-alpha.png": _png(_resized(with_alpha(lockup),
                                                   LOCKUP_WIDTH)),
            "logo-mark.png": _png(_resized(with_alpha(mark), MARK_WIDTH)),
            "logo-mark-128.png": _png(_resized(with_alpha(mark),
                                               MARK_SMALL_WIDTH)),
            "favicon.png": _png(_resized(with_alpha(mark), FAVICON)),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the derived assets are stale")
    args = parser.parse_args(argv)

    built = build()
    if args.check:
        stale = [name for name, data in built.items()
                 if not (BRAND / name).exists()
                 or (BRAND / name).read_bytes() != data]
        if stale:
            print("brand assets ARE STALE: %s. Run: "
                  "python -m tools.build_brand_assets" % ", ".join(stale))
            return 1
        print("brand assets are up to date.")
        return 0

    for name, data in built.items():
        (BRAND / name).write_bytes(data)
        with Image.open(io.BytesIO(data)) as rendered:
            print("  %-22s %4d x %-4d  %6.0f KB   %s"
                  % (name, rendered.width, rendered.height, len(data) / 1024,
                     DERIVED[name]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
