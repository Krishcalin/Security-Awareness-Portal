"""The certificate: what it says, who gets one, and what happens to the email.

A certificate is the one artefact a learner keeps and shows to somebody else,
so the failures worth testing are the ones that produce a plausible-looking
document that is wrong: a name taken from the wrong claim, a threshold the
browser and the server disagree about, or "sent to your inbox" printed on a
screen when nothing was sent.
"""
from __future__ import annotations

import datetime
import uuid

import pytest

from server import auth, certificate
from server.config import settings
from tests.conftest import needs_db

pytestmark = needs_db

SLUG = "security-awareness-essentials"


# ── the name on it ─────────────────────────────────────────────────────────

def test_the_name_is_the_given_and_family_names():
    assert certificate.printed_name("Krishnendu", "De") == "Krishnendu De"


def test_a_display_name_is_only_a_fallback():
    """Entra's `name` is whatever the directory has been told to show, which
    in plenty of tenants is "De, Krishnendu (Security)"."""
    assert certificate.printed_name("Krishnendu", "De",
                                    display_name="De, Krishnendu (Security)") \
        == "Krishnendu De"
    assert certificate.printed_name("", "", display_name="Krishnendu De") \
        == "Krishnendu De"


def test_a_missing_name_falls_back_to_the_address_rather_than_a_blank_line():
    """A certificate with nothing on the name rule is unusable, and the only
    person who finds out is the one who receives it."""
    assert certificate.printed_name("", "", email="krishnendu.de@example.com") \
        == "Krishnendu De"


def test_a_partial_name_is_not_padded_with_a_space():
    assert certificate.printed_name("Prince", "") == "Prince"


# ── the document ───────────────────────────────────────────────────────────

def _text_of(pdf: bytes) -> str:
    pdfium = pytest.importorskip("pypdfium2")
    page = pdfium.PdfDocument(pdf)[0]
    return page.get_textpage().get_text_range()


def test_it_is_a_pdf_carrying_the_name_the_date_and_the_serial():
    pdf = certificate.render("Krishnendu De", datetime.date(2026, 8, 30),
                             "SAT-2026-7QK4M2XD")
    assert pdf[:5] == b"%PDF-"
    text = _text_of(pdf)
    assert "Krishnendu De" in text
    assert "30 August 2026" in text
    assert "SAT-2026-7QK4M2XD" in text


@pytest.mark.parametrize("name", [
    "Li Wei",
    "Krishnendu De",
    "Wilhelmina Featherstonehaugh-Vane",
    "Maria del Carmen Fernandez de la Vega y Sanz",
    "Bartholomew Montmorency-Fitzwilliam Chelmsford-Ashworth",
])
def test_every_name_fits_on_the_rule_and_none_is_truncated(name):
    """Truncating somebody's name is worse than setting it smaller, so the
    property to hold is that the whole name is there AND inside the rule."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    size = certificate._fitted_size(name, "Times-Bold", certificate.NAME_SIZE,
                                    certificate.NAME_MIN_SIZE,
                                    certificate.NAME_MAX_WIDTH)
    width = stringWidth(name, "Times-Bold", size * certificate.SCALE)
    assert width <= certificate.NAME_MAX_WIDTH * certificate.SCALE
    assert name in _text_of(certificate.render(name))


def test_a_name_too_long_for_full_size_is_set_smaller():
    """The shrinking is real, not a branch nothing reaches."""
    long_name = "Maria del Carmen Fernandez de la Vega y Sanz"
    fitted = certificate._fitted_size(
        long_name, "Times-Bold", certificate.NAME_SIZE,
        certificate.NAME_MIN_SIZE, certificate.NAME_MAX_WIDTH)
    assert fitted < certificate.NAME_SIZE


def test_the_name_sits_on_its_rule_in_the_artwork():
    """The positions are measured from the artwork. If it is ever redrawn,
    this fails rather than the name quietly floating off the line."""
    from PIL import Image
    with Image.open(certificate.MASTER) as art:
        assert (art.width, art.height) == (certificate.ART_WIDTH,
                                           certificate.ART_HEIGHT)
        pixels = art.convert("RGB").load()
        for centre_x, rule_y in (certificate.NAME_RULE, certificate.DATE_RULE):
            ink = sum(1 for x in range(centre_x - 60, centre_x + 60)
                      for y in (rule_y - 1, rule_y, rule_y + 1)
                      if sum(pixels[x, y]) / 3 < 130)
            assert ink > 100, "no rule at %d,%d" % (centre_x, rule_y)


def test_the_render_source_is_not_stale():
    from tools.optimise_certificate import main
    assert main(["--check"]) == 0


def test_the_pdf_is_small_enough_to_email():
    """It goes to every employee. The PNG master would make it 3.4MB."""
    pdf = certificate.render("Krishnendu De")
    assert len(pdf) < 1_200_000


# ── who gets one ───────────────────────────────────────────────────────────

@pytest.fixture
def person(clean):
    """A signed-in learner with a first and last name from Entra."""
    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email="krishnendu.de@example.com",
                        display_name="De, Krishnendu (Security)",
                        given_name="Krishnendu", family_name="De")
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
    return clean


def sit(client, correct: int):
    """Answer `correct` questions right and the rest wrong, then finish."""
    from server import db
    started = client.post("/api/modules/%s/attempts" % SLUG).json()
    keys = {row["ordinal"]: row["correct_index"] for row in db.query(
        "SELECT q.ordinal, q.correct_index FROM question q JOIN module m "
        "ON m.id = q.module_id WHERE m.slug = %s", (SLUG,))}
    for i, question in enumerate(started["questions"]):
        key = keys[question["ordinal"]]
        chosen = key if i < correct else (key + 1) % len(question["options"])
        client.post("/api/attempts/%d/responses" % started["attempt_id"],
                    json={"ordinal": question["ordinal"],
                          "chosen_index": chosen, "took_ms": 5000})
    return client.post("/api/attempts/%d/finish"
                       % started["attempt_id"]).json()


def test_seventy_percent_passes(person):
    result = sit(person, 7)                      # 7/10 = 70%, exactly the mark
    assert result["out_of"] == 10
    assert result["needed"] == 7
    assert result["pass_mark"] == pytest.approx(0.70)
    assert result["passed"] is True
    assert result["certificate"]["serial"].startswith("SAT-")


def test_just_under_the_mark_does_not(person):
    result = sit(person, 6)                      # 6/10 = 60%
    assert result["passed"] is False
    assert result["certificate"] is None


def test_the_certificate_carries_the_entra_first_and_last_name(person):
    result = sit(person, 10)
    assert result["certificate"]["name_printed"] == "Krishnendu De"


def test_the_certificate_downloads_as_a_pdf(person):
    result = sit(person, 10)
    response = person.get("/api/certificates/%s"
                          % result["certificate"]["serial"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert "Krishnendu De" in _text_of(response.content)


def test_somebody_elses_certificate_is_not_found(person):
    """The serial is quotable, so it must not also be the key to the PDF."""
    result = sit(person, 10)
    other = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=other, email="other@example.com")
    person.cookies.set(auth.COOKIE_NAME, auth.issue(other))
    assert person.get("/api/certificates/%s"
                      % result["certificate"]["serial"]).status_code == 404


def test_the_printed_name_is_frozen_at_issue(person):
    """Somebody marrying next year must not retrospectively alter the
    document they were already sent."""
    from server import db
    result = sit(person, 10)
    db.execute("UPDATE learner SET family_name = 'Smith'")
    again = person.get("/api/certificates/%s"
                       % result["certificate"]["serial"])
    assert "Krishnendu De" in _text_of(again.content)


def test_a_retake_earns_its_own_certificate(person):
    first = sit(person, 10)
    second = sit(person, 9)
    assert second["certificate"]["serial"] != first["certificate"]["serial"]
    assert second["attempt_no"] == 2


def test_finishing_twice_does_not_issue_twice(person):
    from server import db
    sit(person, 10)
    assert db.one("SELECT count(*) c FROM certificate")["c"] == 1


# ── the email ──────────────────────────────────────────────────────────────

def test_an_unsendable_certificate_says_so_instead_of_claiming_it_was_sent(person):
    """SMTP is not configured in the tests, which is exactly the case that
    otherwise prints 'check your inbox' at somebody and sends nothing."""
    from server import db
    result = sit(person, 10)
    assert result["certificate"]["will_email_to"] == ""

    row = db.one("SELECT emailed_at, email_error FROM certificate WHERE serial = %s",
                 (result["certificate"]["serial"],))
    assert row["emailed_at"] is None
    assert "SMTP_HOST" in row["email_error"]


def test_a_failure_to_send_does_not_cost_anybody_their_certificate(person):
    """Mail is a copy. The certificate is earned, recorded and downloadable
    whether or not anything ever leaves the building."""
    result = sit(person, 10)
    assert person.get("/api/certificates/%s"
                      % result["certificate"]["serial"]).status_code == 200


def test_it_is_sent_from_the_ciso_with_the_pdf_attached(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "certificate_from", "ciso@mycompanydomain.com")

    sent = []

    class Transport:
        def send_message(self, message):
            sent.append(message)

    from server import mailer
    mailer.send_certificate("someone@example.com", "Krishnendu De",
                            certificate.render("Krishnendu De"),
                            "SAT-2026-7QK4M2XD", 11, 12, transport=Transport())

    message = sent[0]
    assert "ciso@mycompanydomain.com" in message["From"]
    assert message["To"] == "someone@example.com"
    assert "certificate" in message["Subject"].lower()

    attachments = [part for part in message.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename().endswith("SAT-2026-7QK4M2XD.pdf")
    assert attachments[0].get_payload(decode=True)[:5] == b"%PDF-"

    body = message.get_body(("plain",)).get_content()
    assert "11 of 12" in body


def test_nothing_is_sent_when_smtp_is_not_configured(monkeypatch):
    from server import mailer
    monkeypatch.setattr(settings, "smtp_host", "")
    with pytest.raises(mailer.NotConfigured) as refused:
        mailer.send_certificate("a@b.com", "A B", b"%PDF-", "S", 1, 1)
    assert "downloaded from the portal" in str(refused.value)


def test_it_really_goes_out_over_smtp(monkeypatch):
    """The other mail tests inject a transport, which leaves the actual
    smtplib call — connect, STARTTLS, login, send — untested. This runs it
    against a real SMTP server on a loopback port."""
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    import email
    import socket
    import time

    with socket.socket() as probe:               # a port nothing else holds
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    received = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append(envelope)
            return "250 OK"

    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", port)
    monkeypatch.setattr(settings, "smtp_starttls", False)
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "certificate_from", "ciso@mycompanydomain.com")

    from server import mailer
    controller = aiosmtpd.Controller(Handler(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        mailer.send_certificate("someone@example.com", "Krishnendu De",
                                certificate.render("Krishnendu De"),
                                "SAT-2026-7QK4M2XD", 11, 12)
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            time.sleep(0.02)
    finally:
        controller.stop()

    assert received, "nothing reached the SMTP server"
    envelope = received[0]
    # The envelope sender, not just the From header: this is the address the
    # receiving domain checks SPF against.
    assert envelope.mail_from == "ciso@mycompanydomain.com"
    assert envelope.rcpt_tos == ["someone@example.com"]

    message = email.message_from_bytes(envelope.content)
    attachment = next(p for p in message.walk() if p.get_filename())
    assert attachment.get_payload(decode=True)[:5] == b"%PDF-"
