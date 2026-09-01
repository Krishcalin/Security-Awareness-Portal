"""Test configuration.

The environment is set here, before anything imports `server.config`, because
settings are read once at import. The database is a separate one from the
development database: a test suite that truncates tables is a test suite you
do not want pointed at the data you were just looking at.
"""
from __future__ import annotations

import os
import uuid

import pytest

DEV_DSN = os.environ.get(
    "DB_DSN", "postgresql://awareness:awareness@127.0.0.1:5434/awareness")
TEST_DB = "awareness_test"
TEST_DSN = DEV_DSN.rsplit("/", 1)[0] + "/" + TEST_DB

os.environ["DB_DSN"] = TEST_DSN
os.environ.setdefault("SESSION_SECRET", "test-secret-not-a-real-one")


def _ensure_test_database() -> str:
    """Create the test database if it is not there. Returns a skip reason, or
    an empty string when the database is ready."""
    import psycopg
    admin = DEV_DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (TEST_DB,)).fetchone()
            if not exists:
                conn.execute('CREATE DATABASE "%s"' % TEST_DB)
    except Exception as problem:                      # noqa: BLE001
        return ("no database at %s (%s). Start it with `docker compose up -d`."
                % (admin, problem.__class__.__name__))
    return ""


SKIP_REASON = _ensure_test_database()
needs_db = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "ok")


@pytest.fixture(scope="session")
def app_client():
    """A client with the app's lifespan run: schema applied, content loaded."""
    from fastapi.testclient import TestClient
    from server.api import app
    # https, because the session cookie is Secure and a client speaking http
    # silently DISCARDS it — which reads as "not signed in" in a way that a
    # leftover cookie from an earlier test can accidentally paper over.
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def clean(app_client):
    """Wipe the people-and-answers tables between tests, keep the content.

    Content is reloaded by the lifespan once per session; truncating it here
    would make every test pay for an ingest it did not ask for.
    """
    from server import db
    db.execute("TRUNCATE response, attempt, enrolment, learner "
               "RESTART IDENTITY CASCADE")
    # roster_entry hangs off module, not learner, so the cascade above does not
    # reach it. A roster left behind changes the denominator of the next test's
    # report, which is a failure that would look like a bug in the report.
    db.execute("TRUNCATE roster_entry RESTART IDENTITY")
    # The client is shared for the whole session, so its cookie jar outlives
    # each test. A cookie the SERVER set in an earlier test is not replaced by
    # a later `cookies.set` of the same name — httpx keeps both — and the
    # request then goes out as somebody who no longer exists.
    app_client.cookies.clear()
    return app_client


@pytest.fixture
def signed_in(clean):
    """A client carrying a valid session for a freshly created learner."""
    from server import auth
    oid = str(uuid.uuid4())
    learner = auth.upsert_learner(entra_oid=oid, email="test@example.com",
                                  upn="test@example.com",
                                  display_name="Test Person",
                                  department="Finance")
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
    clean.learner = learner
    return clean
