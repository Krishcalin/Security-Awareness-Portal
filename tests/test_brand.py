"""The brand assets, and the pages that reference them.

A broken logo is not a broken build. Everything still works, the page just
looks like it was left half-finished — and the one screen this matters most on
is the sign-in page, where a portal that looks unfinished is a portal people
are right to be suspicious of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import build_brand_assets as brand

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"


def test_the_derived_assets_are_not_stale():
    """They come from the master, so an edited master with an unbuilt
    derivative means the site is showing the old logo."""
    assert brand.main(["--check"]) == 0


def test_the_master_is_kept_untouched():
    """It is the file that was handed over. Everything else is derived."""
    assert brand.MASTER.exists()
    from PIL import Image
    with Image.open(brand.MASTER) as master:
        assert (master.width, master.height) == (2400, 1792)


@pytest.mark.parametrize("name", sorted(brand.DERIVED))
def test_each_derived_asset_exists_and_is_a_png(name):
    from PIL import Image
    path = BRAND / name
    assert path.exists(), name
    with Image.open(path) as image:
        assert image.format == "PNG"


def test_the_lockup_is_trimmed_to_its_own_artwork():
    """The master is a lockup on a page of white. A layout cannot space
    something that brings its own margin."""
    from PIL import Image
    with Image.open(brand.MASTER) as master:
        page = master.width / master.height
    with Image.open(BRAND / "logo-lockup.png") as lockup:
        trimmed = lockup.width / lockup.height
    assert trimmed > page * 2, "the lockup still carries the page around it"


def test_the_transparent_versions_really_are_transparent():
    from PIL import Image
    for name in ("logo-lockup-alpha.png", "logo-mark.png", "favicon.png"):
        with Image.open(BRAND / name) as image:
            assert image.mode == "RGBA", name
            alphas = image.getchannel("A").getextrema()
            assert alphas[0] == 0, "%s has no transparent pixel" % name
            assert alphas[1] == 255, "%s is not opaque anywhere" % name


def test_the_mark_is_the_shield_without_the_words():
    """Found by looking for the gap in the lockup rather than a hard-coded x,
    so it survives the lockup being re-set with different spacing."""
    from PIL import Image
    with Image.open(BRAND / "logo-mark.png") as mark:
        shape = mark.width / mark.height
    # The shield is a little taller than it is wide; the lockup is 3:1.
    assert 0.6 < shape < 1.2, "the mark is not shield-shaped (%.2f)" % shape


def test_the_header_asset_is_small_enough_to_load_on_every_page():
    """The 512px mark is 320KB, which is a lot to spend drawing something 28
    pixels across on every single page view."""
    assert (BRAND / "logo-mark-128.png").stat().st_size < 64_000
    assert (BRAND / "favicon.png").stat().st_size < 24_000


def test_every_brand_file_the_app_asks_for_is_one_that_exists():
    """A path typed into a template or a component is not checked by anything
    else until somebody opens the page."""
    referenced = set()
    for source in (ROOT / "server" / "templates" / "index.html",
                   ROOT / "frontend" / "index.html",
                   ROOT / "frontend" / "src" / "routes" / "Shell.tsx"):
        import re
        referenced |= set(re.findall(r"/media/brand/([\w.-]+)",
                                     source.read_text(encoding="utf-8")))
    assert referenced, "nothing references the brand assets at all"
    for name in referenced:
        assert (BRAND / name).exists(), name
