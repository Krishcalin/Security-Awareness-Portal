"""Who is asking, and how the server knows.

NOTHING IN THE HTTP SURFACE CREATES A SESSION. There is no login endpoint here
that takes an email and believes it — the sort of thing that is added "just
for development" and is still there two years later, accepting any address
anybody types. The only caller of `issue()` will be the Entra ID sign-in flow,
and until that lands the only way to obtain a session is
`python -m server.devsession`, which needs shell access to the machine and the
session secret. That is not a way in from outside.

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


def issue(entra_oid: str, now: Optional[float] = None) -> str:
    """Mint a session for an identity Entra has already vouched for."""
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to sign")
    issued = int(now if now is not None else time.time())
    body = _b64(json.dumps({"oid": entra_oid, "iat": issued},
                           separators=(",", ":")).encode("utf-8"))
    return body + "." + _signature(body)


def read(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The claims in a token, or None if it is not one we signed, or expired.

    Returns None for every failure rather than raising per cause: a caller
    that can tell a bad signature from an expired one is a caller that can be
    asked which it was.
    """
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    if not hmac.compare_digest(signature, _signature(body)):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    issued = claims.get("iat")
    if not isinstance(issued, int):
        return None
    age = (now if now is not None else time.time()) - issued
    if age < 0 or age > MAX_AGE_SECONDS:
        return None
    return claims


def upsert_learner(entra_oid: str, email: str, upn: str = "",
                   display_name: str = "", department: str = "") -> Dict[str, Any]:
    """Find or create the person behind an Entra identity.

    Matched on `entra_oid`, which Entra guarantees is immutable, and never on
    email: an address changes on marriage, transfer or a rebrand of the
    company domain, and matching on it would hand the same person a second,
    empty training record on the day their name changed.
    """
    return db.one(
        """
        INSERT INTO learner (entra_oid, email, upn, display_name, department)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (entra_oid) DO UPDATE SET
            email        = EXCLUDED.email,
            upn          = EXCLUDED.upn,
            display_name = EXCLUDED.display_name,
            department   = COALESCE(NULLIF(EXCLUDED.department, ''),
                                    learner.department)
        RETURNING id, email, entra_oid, upn, display_name, department
        """,
        (entra_oid, email, upn, display_name, department))


def current_learner(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in learner, or 401."""
    claims = read(request.cookies.get(COOKIE_NAME, ""))
    if not claims:
        raise HTTPException(status_code=401, detail="not signed in")
    learner = db.one(
        "SELECT id, email, entra_oid, upn, display_name, department "
        "FROM learner WHERE entra_oid = %s", (claims["oid"],))
    if not learner:
        # A validly signed session for somebody who is no longer in the
        # database — a leaver, or a restored backup. Treat as signed out.
        raise HTTPException(status_code=401, detail="not signed in")
    return learner
