"""Signing in with Microsoft Entra ID.

The authorisation-code flow with PKCE, driven by MSAL — Microsoft's own
library — rather than by hand. Validating an ID token means checking a
signature against a rotating JWKS, the issuer, the audience, the expiry and
the nonce, and every one of those is a place where a bespoke implementation
quietly accepts a token it should not.

Four things this file is careful about, all of which are ways an SSO
integration lets in somebody it should not:

  AUTHORITY IS THE TENANT, NOT `common`. Pointed at `common`, the app accepts
  a valid token from ANY Microsoft tenant in the world — every one of those
  users is a real, correctly signed Microsoft identity. The `tid` claim is
  then checked as well, because the authority is a request and the claim is
  the answer.

  THE FLOW STATE IS SIGNED. `state`, `nonce` and the PKCE verifier have to
  survive the round trip to Microsoft, and there is no server-side session for
  somebody who has not signed in yet. They travel in a short-lived signed
  cookie, so a callback carrying a state this server never issued is refused
  rather than processed.

  THE RETURN PATH IS A PATH. `?next=` is an open redirect the moment it is
  allowed to be a URL: a link to the real site, a real Microsoft sign-in, then
  a bounce to somewhere that looks like the site and asks for a password.

  IDENTITY IS `oid`. Not email, not UPN, both of which change when somebody
  marries, transfers, or the company rebrands its domain — and matching on
  them would hand that person a second, empty training record on the day it
  happened.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import msal

from server.config import settings

log = logging.getLogger(__name__)

#: Just sign-in. This portal reads nothing from Microsoft Graph, so it asks
#: for nothing beyond who the person is: an integration that requests more
#: than it uses is a consent screen nobody can sensibly approve.
SCOPES: list[str] = []

#: How long somebody has between being sent to Microsoft and coming back.
FLOW_COOKIE = "awareness_flow"
FLOW_MAX_AGE_SECONDS = 10 * 60


class SignInRefused(Exception):
    """Sign-in did not complete, with a reason fit to show a person."""


def configured() -> bool:
    return bool(settings.entra_tenant_id and settings.entra_client_id
                and settings.entra_client_secret and settings.entra_redirect_uri)


def authority() -> str:
    # The tenant, explicitly. See the note at the top of this file.
    return "https://login.microsoftonline.com/" + settings.entra_tenant_id


def _app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        settings.entra_client_id,
        authority=authority(),
        client_credential=settings.entra_client_secret,
    )


def safe_next(candidate: Optional[str]) -> str:
    """A path inside this site, or the front page.

    Anything with a scheme or a host is discarded, as is `//host`, which most
    browsers read as protocol-relative and which is the form that gets past a
    naive "must start with /" check.
    """
    if not candidate or not candidate.startswith("/"):
        return "/"
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    return candidate


def begin(next_path: str = "/") -> Tuple[str, Dict[str, Any]]:
    """Where to send the browser, and the flow state to remember."""
    flow = _app().initiate_auth_code_flow(
        SCOPES, redirect_uri=settings.entra_redirect_uri)
    flow["next"] = safe_next(next_path)
    return flow["auth_uri"], flow


def complete(flow: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
    """Exchange the code for a validated identity.

    MSAL checks the state, the nonce and the ID token's signature, issuer,
    audience and expiry. What is left to check here is the tenant.
    """
    result = _app().acquire_token_by_auth_code_flow(flow, query)
    if "error" in result:
        # `error_description` from Microsoft is long and aimed at developers;
        # the short code is the part worth logging and the part worth showing.
        log.warning("entra sign-in failed: %s", result.get("error_description"))
        raise SignInRefused(str(result.get("error", "sign-in failed")))

    claims = result.get("id_token_claims") or {}
    tenant = claims.get("tid")
    if tenant != settings.entra_tenant_id:
        # A correctly signed token from the wrong directory. This is what a
        # misconfigured multi-tenant registration looks like from the inside:
        # a real Microsoft user, and not one of ours.
        log.warning("rejected a sign-in from tenant %s", tenant)
        raise SignInRefused("this account is not in your organisation's directory")

    oid = claims.get("oid")
    if not oid:
        raise SignInRefused("Microsoft did not return an object id for this account")

    # Only claimed when the tenant is configured to say so. Without the
    # group, `role` stays None and whatever is already on the record wins —
    # so a missing claim never quietly demotes somebody.
    role = None
    if settings.entra_admin_group:
        groups = claims.get("groups") or []
        role = "admin" if settings.entra_admin_group in groups else "learner"

    return {
        "oid": oid,
        "role": role,
        # Separately, not parsed out of `name`: a display name is whatever the
        # directory has been told to show, and splitting "de, Krishnendu
        # (Security)" on a space puts the wrong thing on a certificate.
        "given_name": claims.get("given_name", ""),
        "family_name": claims.get("family_name", ""),
        # `preferred_username` is the UPN in Entra and is a display attribute:
        # kept for support, never used to match a person to their record.
        "upn": claims.get("preferred_username", ""),
        "email": claims.get("email") or claims.get("preferred_username", ""),
        "display_name": claims.get("name", ""),
    }
