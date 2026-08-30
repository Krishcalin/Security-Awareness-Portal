"""Who is asking, and how the server knows.

TWO THINGS CREATE A SESSION, and both prove something before they do.

A completed **Microsoft Entra sign-in**, which is the way in for anybody the
directory covers: it carries the organisation's own MFA and conditional
access, and there is no password here to phish.

A **password**, for the people it does not — contractors, shift staff on
shared terminals, site engineers with a payroll number and no mailbox. Checked
against a scrypt hash by `password_sign_in` below, with a lockout, and issued
only against a learner row somebody provisioned from the shell. There is still
no endpoint that takes an email address and believes it: `python -m
server.account` creates the account, and `/auth/dev` remains a way to redeem a
token already signed with SESSION_SECRET rather than a way in.

The cookie is a signed statement, not a store. It carries one claim naming the
identity — `oid` for a directory sign-in, `lid` for a local account — and the
server looks up everything else. Which claim it is matters: a local session
can never become a directory session because somebody later created an address
in the tenant.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from server import db, passwords
from server.config import settings

COOKIE_NAME = "awareness_session"

#: Consecutive failures before an account stops answering, and for how long.
#:
#: Per account rather than per address seen, which is the trade worth naming:
#: it does nothing against somebody spraying one common password across a
#: thousand addresses, and everything against the attack that actually gets in
#: — one address, a word list, all night. It also means the lockout message
#: only ever appears to somebody who has already failed ten times against that
#: one account, so it is not a way to ask whether an address exists here.
LOCKOUT_AFTER = 10
LOCKOUT_MINUTES = 15

#: How long a sign-in form stays good for. Long enough to leave the tab open
#: over lunch, short enough that a form scraped from a page today is not
#: submittable next week.
FORM_MAX_AGE_SECONDS = 4 * 60 * 60

#: The columns that describe a learner. `password_hash` is deliberately not
#: among them: `current_learner` hands this dictionary to route handlers, and
#: a hash that is never loaded is a hash that cannot be returned by one.
LEARNER_COLUMNS = ("id, email, entra_oid, upn, display_name, department, "
                   "given_name, family_name, role, password_must_change, "
                   "session_epoch")


class Refused(Exception):
    """A sign-in that will not be granted, with a sentence to show the person.

    One message for a wrong password and for an address with no account, on
    purpose \u2014 two different messages is a way to ask which addresses exist.
    """

#: A working day. Long enough to finish a module without signing in twice,
#: short enough that a shared machine does not stay signed in overnight.
MAX_AGE_SECONDS = 10 * 60 * 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(body: str) -> str:
    return _b64(hmac.new(settings.session_secret.encode("utf-8"),
                         body.encode("ascii"), hashlib.sha256).digest())


def sign(claims: Dict[str, Any], now: Optional[float] = None) -> str:
    """A signed, timestamped statement. Not a store — nothing secret goes in.

    Used for two things with very different lifetimes: the session itself, and
    the ten-minute round trip to Microsoft and back.
    """
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to sign")
    payload = dict(claims, iat=int(now if now is not None else time.time()))
    body = _b64(json.dumps(payload, separators=(",", ":"),
                           sort_keys=True).encode("utf-8"))
    return body + "." + _signature(body)


def verify(token: str, max_age: int,
           now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The claims in a token, or None if we did not sign it, or it has expired.

    None for every failure rather than a different exception per cause: a
    caller that can tell a forged signature from an expired one is a caller
    that can be asked which it was.
    """
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    if not hmac.compare_digest(signature, _signature(body)):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    issued = claims.get("iat")
    if not isinstance(issued, int):
        return None
    age = (now if now is not None else time.time()) - issued
    if age < 0 or age > max_age:
        return None
    return claims


def issue(entra_oid: str, epoch: int = 0,
          now: Optional[float] = None) -> str:
    """Mint a session for an identity Entra has already vouched for."""
    return sign({"oid": entra_oid, "ep": int(epoch)}, now=now)


def issue_local(learner_id: int, epoch: int = 0,
                now: Optional[float] = None) -> str:
    """Mint a session for a local account, named by its row rather than by an
    address: an email is editable and a primary key is not."""
    return sign({"lid": int(learner_id), "ep": int(epoch)}, now=now)


def revoke_sessions(learner_id: int) -> None:
    """End every session this learner holds, now rather than in ten hours."""
    db.execute("UPDATE learner SET session_epoch = session_epoch + 1 "
               "WHERE id = %s", (learner_id,))


def read(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The claims in a session cookie, if it is still a valid one."""
    return verify(token, MAX_AGE_SECONDS, now=now)

# ── passwords ──────────────────────────────────────────────────────────────

#: Shown for a wrong password AND for an address with no account here. The
#: difference between those two is exactly what somebody testing a list of
#: addresses wants to learn.
WRONG = "That email address and password do not match an account here."


def password_sign_in(email: str, plain: str,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """The learner behind these credentials, or `Refused` with a reason.

    Every path through here does the same amount of work. An address with no
    account burns the time a real check would have taken, because "no such
    address" answering in a millisecond while "wrong password" takes a sixth
    of a second is a list of who works here, readable with a stopwatch.
    """
    now = now or datetime.now(timezone.utc)
    row = db.one(
        "SELECT id, email, password_hash, password_must_change, "
        "       failed_signins, locked_until "
        "FROM learner WHERE lower(email) = lower(%s)",
        (email.strip(),))

    if not row or not row["password_hash"]:
        # No account, or an Entra-only account with no password set. Both are
        # the same answer, and both take the same time to give.
        passwords.burn_time()
        raise Refused(WRONG)

    locked = row["locked_until"]
    if locked and locked > now:
        minutes = max(1, int((locked - now).total_seconds() // 60) + 1)
        raise Refused(
            "Too many attempts. This account is locked for another %d "
            "minute%s. If it was not you, tell the security team."
            % (minutes, "" if minutes == 1 else "s"))

    # A lock that has run out starts the count again, rather than leaving the
    # next single mistake to lock the account straight back up.
    failures = 0 if locked else row["failed_signins"]

    if not passwords.verify_password(row["password_hash"], plain):
        failures += 1
        db.execute(
            "UPDATE learner SET failed_signins = %s, locked_until = %s "
            "WHERE id = %s",
            (failures,
             now + timedelta(minutes=LOCKOUT_MINUTES)
             if failures >= LOCKOUT_AFTER else None,
             row["id"]))
        raise Refused(WRONG)

    # Right. Clear the count, and take the chance to re-derive a hash made
    # with weaker parameters than we now use — the only moment the plaintext
    # is available to do it with.
    if passwords.needs_rehash(row["password_hash"]):
        db.execute("UPDATE learner SET password_hash = %s WHERE id = %s",
                   (passwords.hash_password(plain), row["id"]))
    db.execute(
        "UPDATE learner SET failed_signins = 0, locked_until = NULL "
        "WHERE id = %s", (row["id"],))
    return db.one("SELECT %s FROM learner WHERE id = %%s" % LEARNER_COLUMNS,
                  (row["id"],))


def set_password(learner_id: int, plain: str, must_change: bool = False) -> None:
    """Store a new password for this learner, and unlock the account.

    Unlocking matters: somebody who has been locked out is often exactly the
    person ringing up to have their password reset, and a reset that leaves
    them locked for another quarter of an hour reads as a broken portal.
    """
    db.execute(
        "UPDATE learner SET password_hash = %s, password_set_at = now(), "
        "       password_must_change = %s, failed_signins = 0, "
        "       locked_until = NULL, "
        # Any session opened with the old password ends here. Somebody
        # resetting a password usually believes the old one is known to
        # somebody else, and leaving that person signed in for another ten
        # hours would answer the wrong half of the problem.
        "       session_epoch = session_epoch + 1 "
        "WHERE id = %s",
        (passwords.hash_password(plain), must_change, learner_id))


def has_password(learner_id: int) -> bool:
    row = db.one("SELECT password_hash FROM learner WHERE id = %s",
                 (learner_id,))
    return bool(row and row["password_hash"])


def upsert_learner(entra_oid: str, email: str, upn: str = "",
                   display_name: str = "", department: str = "",
                   given_name: str = "", family_name: str = "",
                   role: Optional[str] = None) -> Dict[str, Any]:
    """Find or create the person behind an Entra identity.

    Matched on `entra_oid`, which Entra guarantees is immutable, and never on
    email: an address changes on marriage, transfer or a rebrand of the
    company domain, and matching on it would hand the same person a second,
    empty training record on the day their name changed.
    """
    return db.one(
        """
        INSERT INTO learner (entra_oid, email, upn, display_name, department,
                             given_name, family_name, role)
        VALUES (%(entra_oid)s, %(email)s, %(upn)s, %(display_name)s,
                %(department)s, %(given_name)s, %(family_name)s,
                COALESCE(%(role)s, 'learner'))
        ON CONFLICT (entra_oid) DO UPDATE SET
            email        = EXCLUDED.email,
            upn          = EXCLUDED.upn,
            display_name = EXCLUDED.display_name,
            department   = COALESCE(NULLIF(EXCLUDED.department, ''),
                                    learner.department),
            -- Kept if Entra stops sending them: a tenant that turns off the
            -- profile claims should not blank a name already on record.
            given_name   = COALESCE(NULLIF(EXCLUDED.given_name, ''),
                                    learner.given_name),
            family_name  = COALESCE(NULLIF(EXCLUDED.family_name, ''),
                                    learner.family_name),
            -- Only set when the caller has something to say about it. A
            -- sign-in that carries no group claim must not silently demote
            -- somebody who was granted the role from the shell.
            role         = COALESCE(%(role)s, learner.role)
        RETURNING id, email, entra_oid, upn, display_name, department,
                  given_name, family_name, role, session_epoch
        """,
        {"entra_oid": entra_oid, "email": email, "upn": upn,
         "display_name": display_name, "department": department,
         "given_name": given_name, "family_name": family_name, "role": role})


def require_admin(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: somebody allowed to see everybody's results.

    404 rather than 403 for a learner who is not one. A 403 confirms the
    reporting screens exist and are worth coming back for; there is no reason
    to tell somebody that.
    """
    learner = current_learner(request)
    if learner.get("role") != "admin":
        raise HTTPException(status_code=404, detail="not found")
    return learner


def learner_for(claims: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The person a session's claims name, if they are still here.

    `oid` and `lid` are looked up separately and never interchangeably. A
    session minted for a local account names a row; one minted by Entra names
    a directory identity. Resolving either through the other would mean that
    creating an address in the tenant could reach a local account's record.
    """
    if claims.get("oid"):
        learner = db.one("SELECT %s FROM learner WHERE entra_oid = %%s"
                         % LEARNER_COLUMNS, (claims["oid"],))
    elif claims.get("lid"):
        learner = db.one("SELECT %s FROM learner WHERE id = %%s"
                         % LEARNER_COLUMNS, (claims["lid"],))
    else:
        return None

    # A cookie from before the account was closed, or its password taken away.
    # Sessions issued before the current epoch are simply not sessions.
    if learner and claims.get("ep", 0) != learner["session_epoch"]:
        return None
    return learner


def signed_in(request: Request) -> Optional[Dict[str, Any]]:
    """The learner this request carries a session for, or None."""
    claims = read(request.cookies.get(COOKIE_NAME, ""))
    return learner_for(claims) if claims else None


def current_learner(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in learner, or 401."""
    # A validly signed session for somebody no longer in the database — a
    # leaver, or a restored backup — is treated as signed out.
    learner = signed_in(request)
    if not learner:
        raise HTTPException(status_code=401, detail="not signed in")
    return learner
