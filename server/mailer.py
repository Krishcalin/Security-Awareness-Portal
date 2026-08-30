"""Sending the certificate, from the CISO's desk.

Two things this is careful about.

A FAILURE TO SEND IS RECORDED, NOT SWALLOWED. An email that bounced, an email
that was never attempted because SMTP is not configured, and an email that
arrived all look identical from inside the application unless the difference
is written down. `certificate.emailed_at` and `certificate.email_error` are
that difference, and the portal shows the learner which of the three happened
rather than saying "sent to your inbox" and hoping.

THE CERTIFICATE IS NEVER ONLY IN AN EMAIL. It is issued and downloadable the
moment it is earned. Mail is a copy, not the delivery mechanism, so a mail
server having a bad afternoon does not cost somebody the thing they just spent
twenty minutes earning.

One deployment note that belongs with the code rather than only in a README:
sending as an address at a domain whose mail this server does not handle will
be rejected or filed as spam. Being the CISO's address makes that MORE likely,
not less — those domains tend to have a strict DMARC policy, which is the
point of having one. This host has to be an authorised sender in the domain's
SPF record, and the message ought to be DKIM-signed.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from server.config import settings

log = logging.getLogger(__name__)


class NotConfigured(Exception):
    """No SMTP host is set, so nothing was attempted."""


def _body(name: str, score: int, out_of: int) -> tuple[str, str]:
    first = name.split(" ")[0] if name else "there"
    text = (
        "Dear %s,\n\n"
        "Thank you for completing the Security Awareness Training.\n\n"
        "You answered %d of %d questions correctly. Your certificate of "
        "completion is attached.\n\n"
        "The habits in that training are what stop most attacks reaching us: "
        "checking before you click, reporting when something feels wrong, and "
        "never being penalised for raising the alarm. If you are ever unsure "
        "about a message, a call or a request, please report it. Reporting "
        "early is what turns an incident into a non-event.\n\n"
        "With thanks,\n\n"
        "%s\n"
        % (first, score, out_of, settings.certificate_from_name))

    html = (
        '<div style="font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;'
        'color:#171a1f;max-width:34rem">'
        "<p>Dear %s,</p>"
        "<p>Thank you for completing the Security Awareness Training.</p>"
        "<p>You answered <strong>%d of %d</strong> questions correctly. Your "
        "certificate of completion is attached.</p>"
        "<p>The habits in that training are what stop most attacks reaching "
        "us: checking before you click, reporting when something feels wrong, "
        "and never being penalised for raising the alarm. If you are ever "
        "unsure about a message, a call or a request, please report it. "
        "Reporting early is what turns an incident into a non-event.</p>"
        "<p>With thanks,<br>%s</p>"
        "</div>"
        % (first, score, out_of, settings.certificate_from_name))
    return text, html


def build_message(to: str, name: str, pdf: bytes, serial: str,
                  score: int, out_of: int) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.certificate_from_name,
                                  settings.certificate_from))
    message["To"] = to
    message["Subject"] = "Your Security Awareness Training certificate"
    # A reply should reach whoever reads that mailbox, not vanish.
    message["Reply-To"] = settings.certificate_from

    text, html = _body(name, score, out_of)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        pdf, maintype="application", subtype="pdf",
        filename="Security-Awareness-Certificate-%s.pdf" % serial)
    return message


def send_certificate(to: str, name: str, pdf: bytes, serial: str,
                     score: int, out_of: int,
                     transport: Optional[object] = None) -> None:
    """Send it, or raise. The caller records what happened either way."""
    if not settings.mail_configured:
        raise NotConfigured(
            "SMTP_HOST is not set, so no certificate email was sent. The "
            "certificate is issued and can be downloaded from the portal.")
    if not to:
        raise ValueError("no email address for this learner")

    message = build_message(to, name, pdf, serial, score, out_of)

    if transport is not None:                     # used by the tests
        transport.send_message(message)           # type: ignore[attr-defined]
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                      timeout=settings.smtp_timeout) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    log.info("certificate %s emailed", serial)
