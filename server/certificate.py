"""Rendering the certificate of completion.

The supplied artwork is the certificate. This draws two things onto it — the
name and the date — and nothing else, because everything else is already in
the design: the title, the wording, the CHRO signature line and the strapline.

The positions below are measured from the artwork rather than guessed. The
name sits on the rule under "This certifies that", the date on the rule above
"Date of Completion", and both are centred on those rules. If the artwork is
ever redrawn, `tools/measure_certificate.py` re-derives these numbers and the
test asserts they still land on the rules.

Two decisions worth stating:

FONTS ARE THE PDF BASE FOURTEEN. Times and Helvetica need no font file and are
guaranteed to exist in every PDF reader. A bundled TTF would look marginally
better and would render as a silent fallback the first time this runs on a
host that has not got it — on a document a person is going to keep.

A LONG NAME IS SHRUNK, NEVER TRUNCATED. "Wilhelmina Featherstonehaugh-Vane" is
somebody's actual name, and a certificate that prints two thirds of it is
worse than one that prints it slightly smaller.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from reportlab.lib.colors import Color
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

#: The JPEG derived by `tools.optimise_certificate`, not the PNG master.
#: reportlab passes a JPEG into the PDF as it stands and re-encodes a PNG as
#: raw pixels, which is the difference between a 730KB certificate and a 3.4MB
#: one — multiplied by every employee in the organisation, by email.
_ART = Path(__file__).resolve().parents[1] / "assets" / "certificate"
TEMPLATE = _ART / "template.jpg"
MASTER = _ART / "template.png"

#: The artwork, in its own pixels. Every position below is in these units and
#: is scaled to the page at render time.
ART_WIDTH, ART_HEIGHT = 2752, 1536

#: Measured from the artwork: the rule a name is written on, and the rule the
#: date sits above. (centre_x, rule_y)
NAME_RULE = (1375, 965)
DATE_RULE = (1935, 1260)

#: Width of the name rule, less a margin. A name is shrunk to fit inside this.
NAME_MAX_WIDTH = 1560

#: Type sizes, in artwork pixels like everything else here, and converted once
#: at render time. Mixing pixels and points is how a certificate ends up with a
#: name a third the size of the label underneath it.
NAME_SIZE = 96
NAME_MIN_SIZE = 44
DATE_SIZE = 40

#: Sampled from the artwork's own lettering, so the additions do not read as
#: additions.
INK = Color(9 / 255, 26 / 255, 58 / 255)

#: Where the navy border begins along the bottom edge, in artwork pixels.
NAVY_BAND_TOP = 1500

#: The page is the artwork's shape, at A4 landscape width. A certificate is
#: printed "fit to page" by every viewer, and a page in the artwork's own
#: proportions has no band of dead paper along one edge.
PAGE_WIDTH = 297 * mm
PAGE_HEIGHT = PAGE_WIDTH * ART_HEIGHT / ART_WIDTH


#: One conversion, used by everything: artwork pixels to PDF points.
SCALE = PAGE_WIDTH / ART_WIDTH


def _to_page(x: float, y: float) -> Tuple[float, float]:
    """Artwork pixels (origin top-left) to PDF points (origin bottom-left)."""
    return x * SCALE, PAGE_HEIGHT - y * SCALE


def _fitted_size(text: str, font: str, start: float, minimum: float,
                 max_width_px: float) -> float:
    """The largest size, in artwork pixels, at which the name fits its rule."""
    size = start
    limit = max_width_px * SCALE
    while size > minimum and stringWidth(text, font, size * SCALE) > limit:
        size -= 1
    return size


def printed_name(given_name: str, family_name: str,
                 display_name: str = "", email: str = "") -> str:
    """The name to print, and what to do when the directory has not got one.

    First and last name as Entra reports them. Some tenants do not populate
    them — the fallbacks are the display name, then the local part of the
    email address. A certificate with a blank line where a name should be is
    the one outcome worth avoiding, because it is unusable and it is only
    noticed by the person who receives it.
    """
    both = " ".join(part.strip() for part in (given_name, family_name)
                    if part and part.strip())
    if both:
        return both
    if display_name.strip():
        return display_name.strip()
    return (email.split("@")[0] or "").replace(".", " ").title()


def render(name: str, completed_on: Optional[date] = None,
           serial: str = "") -> bytes:
    """The certificate, as a PDF."""
    completed_on = completed_on or date.today()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle("Certificate of Completion - Security Awareness Training")
    pdf.setAuthor("Security Awareness Portal")

    pdf.drawImage(str(TEMPLATE), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT,
                  preserveAspectRatio=False, mask=None)

    pdf.setFillColor(INK)

    size = _fitted_size(name, "Times-Bold", NAME_SIZE, NAME_MIN_SIZE,
                        NAME_MAX_WIDTH)
    centre_x, rule_y = _to_page(*NAME_RULE)
    pdf.setFont("Times-Bold", size * SCALE)
    # Sat above the rule by a quarter of the type size, so the descenders of a
    # "y" or a "g" clear it rather than crossing it.
    pdf.drawCentredString(centre_x, rule_y + size * SCALE * 0.25, name)

    centre_x, rule_y = _to_page(*DATE_RULE)
    pdf.setFont("Helvetica", DATE_SIZE * SCALE)
    pdf.drawCentredString(centre_x, rule_y + DATE_SIZE * SCALE * 0.28,
                          completed_on.strftime("%d %B %Y"))

    if serial:
        # In the navy border at the foot, where it is present without
        # competing with the design — a certificate that cannot be checked
        # against the record is one that has to be taken on trust. The
        # position comes from the artwork, not from a margin in millimetres
        # that would land on the cream if the border were ever redrawn.
        band_top = PAGE_HEIGHT - NAVY_BAND_TOP * SCALE
        pdf.setFont("Helvetica", 5.5)
        pdf.setFillColor(Color(1, 1, 1, alpha=0.6))
        pdf.drawCentredString(PAGE_WIDTH / 2, band_top * 0.32,
                              "Certificate " + serial)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
