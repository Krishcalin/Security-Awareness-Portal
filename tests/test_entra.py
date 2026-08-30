"""Signing in with Microsoft Entra ID.

A real round trip to Microsoft cannot happen in a test, so MSAL is stood in
for. What is worth testing is not the exchange — that is MSAL's job — but the
decisions made around it, which are the ones that let the wrong person in:

  - a callback carrying a state this server never issued
  - a correctly signed token from somebody else's Microsoft tenant
  - `?next=` pointing at another site
  - matching a person on their email address, so that changing it splits their
    training record in two
"""
from __future__ import annotations

import pytest

from server import auth, entra
from server.config import settings
from tests.conftest import needs_db

pytestmark = needs_db

TENANT = "11111111-1111-1111-1111-111111111111"
OID = "22222222-2222-2222-2222-222222222222"


class FakeMicrosoft:
    """Stands in for MSAL. Returns whatever claims the test asks for."""

    def __init__(self, claims=None, error=None):
        self.claims = claims
        self.error = error
        self.seen_flow = None

    def initiate_auth_code_flow(self, scopes, redirect_uri):
        return {
            "auth_uri": "https://login.microsoftonline.com/%s/oauth2/v2.0/"
                        "authorize?client_id=x&state=st" % TENANT,
            "state": "st", "nonce": "no", "code_verifier": "cv",
            "redirect_uri": redirect_uri, "scope": scopes,
        }

    def acquire_token_by_auth_code_flow(self, flow, query):
        self.seen_flow = flow
        if self.error:
            return {"error": self.error, "error_description": "no"}
        return {"id_token_claims": self.claims}


@pytest.fixture
def entra_configured(monkeypatch):
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_client_id", "client")
    monkeypatch.setattr(settings, "entra_client_secret", "secret")
    monkeypatch.setattr(settings, "entra_redirect_uri",
                        "https://portal.example.com/auth/callback")
    monkeypatch.setattr(settings, "cookie_secure", True)


@pytest.fixture
def microsoft(monkeypatch, entra_configured):
    def install(claims=None, error=None):
        fake = FakeMicrosoft(claims, error)
        monkeypatch.setattr(entra, "_app", lambda: fake)
        return fake
    return install


def good_claims(**overrides):
    return {"tid": TENANT, "oid": OID, "name": "Test Person",
            "preferred_username": "test@example.com",
            "email": "test@example.com", **overrides}


# ── where a person is sent afterwards ──────────────────────────────────────

@pytest.mark.parametrize("candidate", [
    "https://evil.example.com/phish",
    "//evil.example.com/phish",        # protocol-relative: gets past "starts with /"
    "/\\evil.example.com",             # backslash, which some browsers normalise
    "http://evil.example.com",
    "",
    None,
    "module/1",                        # relative, so it resolves against /auth/
])
def test_an_off_site_return_path_is_discarded(candidate):
    """`?next=` is an open redirect the moment it can be a URL: a real link to
    a real site, a real Microsoft sign-in, then a bounce somewhere that looks
    like the site and asks for a password."""
    assert entra.safe_next(candidate) == "/"


@pytest.mark.parametrize("candidate", [
    "/", "/module/security-awareness-essentials",
    "/module/security-awareness-essentials?slide=11",
])
def test_a_path_inside_the_site_is_kept(candidate):
    assert entra.safe_next(candidate) == candidate


# ── starting ───────────────────────────────────────────────────────────────

def test_login_says_so_when_entra_is_not_configured(clean):
    response = clean.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert "ENTRA_" in response.text


def test_login_sends_the_browser_to_microsoft(clean, microsoft):
    microsoft()
    response = clean.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://login.microsoftonline.com/")

    cookie = response.cookies[entra.FLOW_COOKIE]
    flow = auth.verify(cookie, entra.FLOW_MAX_AGE_SECONDS)
    assert flow is not None, "the flow cookie is not one this server signed"
    assert flow["state"] and flow["code_verifier"]


def test_the_flow_cookie_does_not_go_to_the_whole_site(clean, microsoft):
    microsoft()
    response = clean.get("/auth/login", follow_redirects=False)
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Path=/auth" in header
    assert "Secure" in header


# ── coming back ────────────────────────────────────────────────────────────

def test_a_callback_that_did_not_start_here_is_refused(clean, microsoft):
    microsoft(good_claims())
    clean.cookies.clear()
    response = clean.get("/auth/callback?code=abc&state=st",
                         follow_redirects=False)
    assert response.status_code == 400
    assert auth.COOKIE_NAME not in response.cookies


def test_a_tampered_flow_cookie_is_refused(clean, microsoft):
    microsoft(good_claims())
    signed = auth.sign({"state": "st", "code_verifier": "cv"})
    body, _, signature = signed.rpartition(".")
    clean.cookies.set(entra.FLOW_COOKIE, body + "." + signature[::-1])
    response = clean.get("/auth/callback?code=abc&state=st",
                         follow_redirects=False)
    assert response.status_code == 400


def test_a_token_from_another_tenant_is_refused(clean, microsoft):
    """A correctly signed token from the wrong directory. This is what a
    misconfigured multi-tenant registration looks like from the inside: a real
    Microsoft user, and not one of ours."""
    microsoft(good_claims(tid="99999999-9999-9999-9999-999999999999"))
    clean.cookies.set(entra.FLOW_COOKIE, auth.sign({"state": "st"}))
    response = clean.get("/auth/callback?code=abc&state=st",
                         follow_redirects=False)
    assert response.status_code == 403
    assert "directory" in response.text
    assert auth.COOKIE_NAME not in response.cookies


def test_a_token_with_no_object_id_is_refused(clean, microsoft):
    claims = good_claims()
    del claims["oid"]
    microsoft(claims)
    clean.cookies.set(entra.FLOW_COOKIE, auth.sign({"state": "st"}))
    assert clean.get("/auth/callback?code=abc&state=st",
                     follow_redirects=False).status_code == 403


def test_microsoft_reporting_an_error_is_refused(clean, microsoft):
    microsoft(error="invalid_grant")
    clean.cookies.set(entra.FLOW_COOKIE, auth.sign({"state": "st"}))
    assert clean.get("/auth/callback?code=abc&state=st",
                     follow_redirects=False).status_code == 403


def test_signing_in_creates_the_learner_and_the_session(clean, microsoft):
    from server import db
    microsoft(good_claims())
    clean.cookies.set(entra.FLOW_COOKIE,
                      auth.sign({"state": "st", "next": "/module/x"}))
    response = clean.get("/auth/callback?code=abc&state=st",
                         follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/module/x"

    header = response.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header and "Secure" in header

    learner = db.one("SELECT * FROM learner WHERE entra_oid = %s", (OID,))
    assert learner["email"] == "test@example.com"
    assert learner["display_name"] == "Test Person"

    claims = auth.read(response.cookies[auth.COOKIE_NAME])
    assert claims and claims["oid"] == OID


def test_an_off_site_next_is_not_followed_after_sign_in(clean, microsoft):
    microsoft(good_claims())
    clean.cookies.set(entra.FLOW_COOKIE,
                      auth.sign({"state": "st",
                                 "next": "https://evil.example.com"}))
    response = clean.get("/auth/callback?code=abc&state=st",
                         follow_redirects=False)
    assert response.headers["location"] == "/"


def test_a_changed_email_does_not_split_the_training_record(clean, microsoft):
    """Somebody marries, or the company rebrands its domain. Matching on email
    would give them a second, empty record on the day it happened."""
    from server import db
    microsoft(good_claims())
    clean.cookies.set(entra.FLOW_COOKIE, auth.sign({"state": "st"}))
    clean.get("/auth/callback?code=abc&state=st", follow_redirects=False)
    first = db.one("SELECT id FROM learner")["id"]

    microsoft(good_claims(email="new.name@example.com",
                          preferred_username="new.name@example.com"))
    clean.cookies.set(entra.FLOW_COOKIE, auth.sign({"state": "st"}))
    clean.get("/auth/callback?code=abc&state=st", follow_redirects=False)

    assert db.one("SELECT count(*) c FROM learner")["c"] == 1
    again = db.one("SELECT id, email FROM learner")
    assert again["id"] == first
    assert again["email"] == "new.name@example.com"


# ── leaving ────────────────────────────────────────────────────────────────

def test_signing_out_clears_the_session(clean):
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(OID))
    response = clean.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert 'awareness_session=""' in response.headers["set-cookie"] or \
           "awareness_session=;" in response.headers["set-cookie"]


def test_signing_out_also_signs_out_at_microsoft(clean, entra_configured):
    """Clearing only the local cookie leaves the Microsoft session, so the
    next click on 'sign in' silently signs the same person back in. On a
    shared machine that is not a sign-out at all."""
    response = clean.get("/auth/logout", follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("https://login.microsoftonline.com/%s" % TENANT)
    assert "logout" in location


def test_signing_out_works_without_entra_configured(clean):
    response = clean.get("/auth/logout", follow_redirects=False)
    assert response.headers["location"] == "/"
