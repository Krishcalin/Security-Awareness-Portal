"""Who is asking, and how the server knows.

ONE THING CREATES A SESSION: a completed Microsoft Entra sign-in. There is no
login endpoint that takes an email address and believes it — the sort of thing
added "just for development" that is still there two years later, accepting
whatever anybody types. `issue()` has exactly two callers: the Entra callback
in `server.api`, and `python -m server.devsession`, which is a command and not
a route, and needs shell access to the machine and the session secret before
it grants anything.

The cookie is a signed statement, not a store: it carries the Entra object id
and nothing else worth stealing, and the server looks up everything else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from server import db
from server.config import settings

COOKIE_NAME = "awareness_session"

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


def issue(entra_oid: str, now: Optional[float] = None) -> str:
    """Mint a session for an identity Entra has already vouched for."""
    return sign({"oid": entra_oid}, now=now)


def read(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The claims in a session cookie, if it is still a valid one."""
    return verify(token, MAX_AGE_SECONDS, now=now)


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
                  given_name, family_name, role
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


def current_learner(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in learner, or 401."""
    claims = read(request.cookies.get(COOKIE_NAME, ""))
    if not claims:
        raise HTTPException(status_code=401, detail="not signed in")
    learner = db.one(
        "SELECT id, email, entra_oid, upn, display_name, department, "
        "given_name, family_name, role FROM learner WHERE entra_oid = %s",
        (claims["oid"],))
    if not learner:
        # A validly signed session for somebody who is no longer in the
        # database — a leaver, or a restored backup. Treat as signed out.
        raise HTTPException(status_code=401, detail="not signed in")
    return learner
