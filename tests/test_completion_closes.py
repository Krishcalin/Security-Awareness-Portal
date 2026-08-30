"""A course that closes once it has been passed.

Every one of these is asserted on the SERVER rather than through the screen.
A rule the browser enforces is a rule that holds until somebody opens the
network tab, and the thing being protected here is a compliance record.

What is deliberately not claimed: that the material becomes unreachable. The
artwork and the recordings are served from `/media`, which is static and
unauthenticated, so anybody holding a URL still has them. This closes the
course and protects the record; it is not a content lock, and pretending
otherwise would be the more dangerous of the two mistakes.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import needs_db

from server import auth

SLUG = "security-awareness-essentials"


@pytest.fixture
def person(clean):
    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email="j.rao@example.com",
                        given_name="Jaya", family_name="Rao")
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


# --------------------------------------------------------------------------
# Before: everything is open
# --------------------------------------------------------------------------

@needs_db
def test_the_deck_is_served_to_somebody_who_has_not_passed(person):
    detail = person.get("/api/modules/%s" % SLUG).json()
    assert len(detail["lessons"]) > 1
    assert detail["completed"] is None


@needs_db
def test_failing_leaves_the_course_open(person):
    """The retake is the whole point. Somebody who scored six out of ten needs
    the material more than anybody, not less."""
    result = sit(person, 6)
    assert result["passed"] is False

    detail = person.get("/api/modules/%s" % SLUG).json()
    assert len(detail["lessons"]) > 1
    assert detail["completed"] is None
    assert person.post("/api/modules/%s/attempts" % SLUG).status_code == 200


# --------------------------------------------------------------------------
# After: the three doors
# --------------------------------------------------------------------------

@needs_db
def test_the_deck_is_not_served_once_it_has_been_passed(person):
    sit(person, 10)
    detail = person.get("/api/modules/%s" % SLUG).json()
    assert detail["lessons"] == []
    # And it says why, with the record itself rather than a flag: this is what
    # the completion screen is drawn from.
    assert detail["completed"]["score"] == 10
    assert detail["completed"]["attempt_no"] == 1
    assert detail["completed"]["serial"]
    assert detail["completed"]["issued_at"]


@needs_db
def test_there_is_no_second_attempt_once_it_has_been_passed(person):
    sit(person, 10)
    refused = person.post("/api/modules/%s/attempts" % SLUG)
    assert refused.status_code == 409
    assert "already been passed" in refused.json()["detail"]


@needs_db
def test_progress_cannot_be_moved_after_a_pass(person):
    """A tab left open from before they passed must not be able to write
    behind the certificate that has already been issued."""
    sit(person, 10)
    refused = person.post("/api/modules/%s/progress" % SLUG,
                          json={"furthest_ordinal": 2})
    assert refused.status_code == 409


@needs_db
def test_resume_does_not_send_them_back_into_it(person):
    """Sending somebody to slide twelve of a course they finished last month
    is the portal telling them it has not noticed."""
    person.post("/api/modules/%s/progress" % SLUG,
                json={"furthest_ordinal": 12})
    assert "slide=12" in person.get("/api/resume").json()["path"]

    sit(person, 10)
    assert person.get("/api/resume").json()["path"] == "/"


@needs_db
def test_the_certificate_is_still_theirs_to_download(person):
    """The course closes; the document it produced does not. Somebody who
    cannot reach their own certificate has been given nothing."""
    result = sit(person, 10)
    serial = result["certificate"]["serial"]
    got = person.get("/api/certificates/%s" % serial)
    assert got.status_code == 200
    assert got.content.startswith(b"%PDF")


@needs_db
def test_the_module_list_still_shows_the_course_as_passed(person):
    """Closed is not hidden. Somebody has to be able to see that they did it,
    and get at the certificate from the same place."""
    sit(person, 10)
    listed = person.get("/api/modules").json()
    mine = [m for m in listed if m["slug"] == SLUG][0]
    assert mine["passed"] is True
    assert mine["certificate_serial"]


# --------------------------------------------------------------------------
# What the administrator sees
# --------------------------------------------------------------------------

@needs_db
def test_the_report_shows_the_date_it_was_passed(person):
    """The question an auditor asks is not "did they" but "when, and can I see
    the document"."""
    from server import db, reporting
    result = sit(person, 10)

    module_id = db.one("SELECT id FROM module WHERE slug = %s", (SLUG,))["id"]
    row = [r for r in reporting.people(module_id)
           if r["email"] == "j.rao@example.com"][0]
    assert row["certificate"] == result["certificate"]["serial"]
    assert row["issued_at"] is not None
    assert row["passed_on_attempt"] == 1

    figures = reporting.summary(module_id)
    assert figures["passed"] == 1
    assert figures["passed_first_time"] == 1


@needs_db
def test_the_export_carries_the_completion(person):
    """The CSV is what gets sent to whoever asks for evidence."""
    from server import auth as server_auth
    sit(person, 10)

    person.cookies.clear()
    boss = server_auth.upsert_learner(
        entra_oid="an-admin", email="ciso@example.com", role="admin")
    person.cookies.set(auth.COOKIE_NAME,
                       auth.issue("an-admin", boss["session_epoch"]))
    csv = person.get("/api/report/%s/export.csv" % SLUG)
    assert csv.status_code == 200
    assert "j.rao@example.com" in csv.text
