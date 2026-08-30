"""Derive the certificate render source from the supplied artwork.

The PNG is the master — it is the file that was handed over, and it stays in
the repository unaltered. But reportlab re-encodes a PNG into the PDF as raw
compressed pixels, which makes every certificate 3.4MB, and that is an
attachment sent to every employee in the organisation.

A JPEG is passed through into the PDF as it stands, so the same certificate is
about 730KB. At quality 92 the difference is not visible on the gold gradient,
which is the part that would show it first.

    python -m tools.optimise_certificate            # rewrite the JPEG
    python -m tools.optimise_certificate --check    # exit 1 if it is stale
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "certificate" / "template.png"
DERIVED = ROOT / "assets" / "certificate" / "template.jpg"

QUALITY = 92


def render_source() -> bytes:
    with Image.open(MASTER) as image:
        buffer = __import__("io").BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=QUALITY,
                                  subsampling=0, optimize=True)
        return buffer.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the derived file is stale")
    args = parser.parse_args(argv)

    data = render_source()
    if args.check:
        current = DERIVED.read_bytes() if DERIVED.exists() else b""
        if current == data:
            print("certificate render source is up to date.")
            return 0
        print("certificate render source IS STALE. Run: "
              "python -m tools.optimise_certificate")
        return 1

    DERIVED.write_bytes(data)
    with Image.open(MASTER) as master:
        print("wrote %s  %d x %d  %.0f KB (from %.0f KB)"
              % (DERIVED.relative_to(ROOT), master.width, master.height,
                 len(data) / 1024, MASTER.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
