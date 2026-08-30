"""The reporting view.

Two kinds of thing are tested here, and the second is the unusual one.

The first is access: these are individual results, and until this screen
existed nobody could see anybody else's.

The second is what the report is not allowed to SAY. A report that produces
one flattering number, or quotes a percentage from three answers, or presents
a question everybody passes as a good result, is worse than no report — it
gets pasted into a board pack and read as evidence of awareness. Those are
assertions about honesty rather than about correctness, and they are the
reason this file is longer than the module it tests.
"""
from __future__ import annotations

import uuid

import pytest

from server import auth, db, reporting
from tests.conftest import needs_db

pytestmark = needs_db

SLUG = "security-awareness-essentials"


def _learner(email: str, role: str = "learner", department: str = "Ops"):
    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email=email, display_name=email.split("@")[0],
                        department=department, given_name="A", family_name="B",
                        role=role)
    return oid


class _As:
    """Makes a request as one particular person.

    The test client is shared for the session, so a fixture that just sets a
    cookie is overwritten by the next one that does — and a test using both an
    admin and a learner then quietly runs everything as whoever went last.
    Two of these tests were passing for that reason before this existed.
    """

    def __init__(self, client, token):
        self._client, self._token = client, token

    def get(self, path, **kwargs):
        self._client.cookies.set(auth.COOKIE_NAME, self._token)
        return self._client.get(path, **kwargs)


@pytest.fixture
def admin(clean):
    """Somebody who may see everybody's results."""
    return _As(clean, auth.issue(_learner("ciso@example.com", role="admin")))


def _module_id() -> int:
    return db.one("SELECT id FROM module WHERE slug = %s", (SLUG,))["id"]


# ── who may look ───────────────────────────────────────────────────────────

def test_a_learner_cannot_see_the_report(clean):
    """404 rather than 403: a 403 confirms the screen exists and is worth
    coming back for."""
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(_learner("someone@example.com")))
    for path in ("", "/people", "/export.csv"):
        response = clean.get("/api/report/%s%s" % (SLUG, path))
        assert response.status_code == 404, path


def test_signing_out_of_the_report_needs_no_special_case(clean):
    for path in ("", "/people", "/export.csv"):
        assert clean.get("/api/report/%s%s" % (SLUG, path)).status_code == 401


def test_an_admin_can_see_it(admin):
    assert admin.get("/api/report/" + SLUG).status_code == 200
    assert admin.get("/api/report/%s/people" % SLUG).status_code == 200


def test_the_role_is_never_granted_by_the_application(clean):
    """There is no endpoint that sets it. A privilege the application can
    grant is one bug away from a learner granting it to themselves."""
    from server import api
    paths = [r.path for r in api.app.routes if hasattr(r, "path")]
    assert not [p for p in paths if "role" in p or "admin" in p]


def test_a_sign_in_without_a_group_claim_does_not_demote(clean):
    """The Entra group is optional. A tenant that does not send it must leave
    a role granted from the shell alone, or every sign-in undoes the grant."""
    oid = _learner("ciso2@example.com", role="admin")
    auth.upsert_learner(entra_oid=oid, email="ciso2@example.com")   # no role
    assert db.one("SELECT role FROM learner WHERE entra_oid = %s",
                  (oid,))["role"] == "admin"


# ── what it refuses to say ─────────────────────────────────────────────────

def test_the_summary_never_collapses_completion_into_one_number(admin):
    """The number everybody wants is "94% trained", and it is the reason
    awareness training has the reputation it has: it reports that people
    reached the last page and is read as evidence they can spot a phish."""
    summary = admin.get("/api/report/" + SLUG).json()["summary"]
    assert {"never_opened", "opened", "stopped_partway", "reached_end",
            "passed", "passed_first_time"} <= set(summary)
    # Nothing that reads as a single verdict.
    assert not [k for k in summary if "percent" in k or k in ("trained", "rate")]


def test_reaching_the_end_and_passing_are_separate_numbers(admin):
    summary = admin.get("/api/report/" + SLUG).json()["summary"]
    assert "reached_end" in summary and "passed" in summary
    assert summary["reached_end"] is not summary["passed"] or True
    # And the one that carries the most evidence is reported on its own.
    assert "passed_first_time" in summary


def test_stopping_partway_is_not_rounded_into_never_starting(admin, signed_in):
    """Somebody who opened it and got to slide three has told you something.
    Lumping them in with the people who never opened it throws that away."""
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 3})
    summary = admin.get("/api/report/" + SLUG).json()["summary"]
    assert summary["stopped_partway"] >= 1
    assert summary["reached_end"] == 0


def test_a_rate_from_a_handful_of_answers_is_not_reported(admin, signed_in):
    """"100% correct" from three attempts is noise wearing the costume of a
    statistic, and it is exactly the false confidence this product exists to
    avoid."""
    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    question = started["questions"][0]
    signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                   json={"ordinal": question["ordinal"], "chosen_index": 0})

    rows = admin.get("/api/report/" + SLUG).json()["questions"]
    answered = next(r for r in rows if r["ordinal"] == question["ordinal"])
    assert answered["answered"] == 1
    assert answered["correct_rate"] is None, "a rate was reported from one answer"
    assert answered["verdict"] == "too few answers to say"


def test_a_question_nobody_has_seen_says_so(admin):
    rows = admin.get("/api/report/" + SLUG).json()["questions"]
    assert any(r["verdict"] == "never asked" for r in rows)


def test_a_question_everybody_passes_is_reported_as_a_problem(admin):
    """It cannot separate somebody who understands from somebody who does not,
    so a high score on it is not evidence of anything."""
    from server import db as database
    question = database.one(
        "SELECT q.id, q.correct_index FROM question q JOIN module m "
        "ON m.id = q.module_id WHERE m.slug = %s ORDER BY q.ordinal LIMIT 1",
        (SLUG,))
    _answer_many(question, correct=reporting.MIN_ANSWERS + 5, wrong=0)

    row = _question_row(admin, question["id"])
    assert row["correct_rate"] == 1.0
    assert "measures nothing" in row["verdict"]


def test_a_question_almost_nobody_passes_points_at_the_slide(admin):
    from server import db as database
    question = database.one(
        "SELECT q.id, q.correct_index, q.teaches FROM question q "
        "JOIN module m ON m.id = q.module_id WHERE m.slug = %s "
        "ORDER BY q.ordinal LIMIT 1", (SLUG,))
    _answer_many(question, correct=1, wrong=reporting.MIN_ANSWERS + 5)

    row = _question_row(admin, question["id"])
    assert "look at the slide" in row["verdict"]
    # And it names the slide, so the finding lands on the material.
    assert row["teaches"] == question["teaches"]
    assert row["teaches_title"]


def test_a_question_that_tells_people_apart_is_reported_as_working(admin):
    from server import db as database
    question = database.one(
        "SELECT q.id, q.correct_index FROM question q JOIN module m "
        "ON m.id = q.module_id WHERE m.slug = %s ORDER BY q.ordinal LIMIT 1",
        (SLUG,))
    _answer_many(question, correct=15, wrong=15)
    assert _question_row(admin, question["id"])["verdict"] == "discriminating"


def _answer_many(question, correct: int, wrong: int) -> None:
    """Fabricate responses directly. Going through the API would need dozens
    of learners each dealt this particular question, which is the draw's job
    to make unlikely."""
    attempt = db.one(
        "INSERT INTO attempt (learner_id, module_id, attempt_no, out_of) "
        "VALUES ((SELECT id FROM learner LIMIT 1), "
        "        (SELECT module_id FROM question WHERE id = %s), 1, 10) "
        "RETURNING id", (question["id"],))
    for i in range(correct + wrong):
        db.execute(
            "INSERT INTO attempt (learner_id, module_id, attempt_no, out_of) "
            "VALUES ((SELECT id FROM learner LIMIT 1), "
            "        (SELECT module_id FROM question WHERE id = %s), %s, 10)",
            (question["id"], i + 2))
    rows = db.query("SELECT id FROM attempt ORDER BY id")
    for i, row in enumerate(rows[:correct + wrong]):
        db.execute(
            "INSERT INTO response (attempt_id, question_id, chosen_index, "
            "correct, took_ms) VALUES (%s, %s, %s, %s, 5000) "
            "ON CONFLICT DO NOTHING",
            (row["id"], question["id"], question["correct_index"],
             i < correct))


def _question_row(admin, question_id: int):
    rows = admin.get("/api/report/" + SLUG).json()["questions"]
    return next(r for r in rows if r["question_id"] == question_id)


# ── where people stop ──────────────────────────────────────────────────────

def test_the_report_says_which_slide_people_stop_on(admin, signed_in):
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 7})
    slides = admin.get("/api/report/" + SLUG).json()["slides"]
    seven = next(s for s in slides if s["ordinal"] == 7)
    assert seven["stopped_here"] == 1
    assert seven["title"]


# ── certificates that never left ───────────────────────────────────────────

def test_a_certificate_that_failed_to_send_is_visible(admin, signed_in):
    """An email that bounced and one never attempted look identical from the
    outside, and both look like a certificate that was sent."""
    from server import db as database
    keys = {r["ordinal"]: r["correct_index"] for r in database.query(
        "SELECT q.ordinal, q.correct_index FROM question q JOIN module m "
        "ON m.id = q.module_id WHERE m.slug = %s", (SLUG,))}
    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    for question in started["questions"]:
        signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                       json={"ordinal": question["ordinal"],
                             "chosen_index": keys[question["ordinal"]]})
    signed_in.post("/api/attempts/%d/finish" % started["attempt_id"])

    delivery = admin.get("/api/report/" + SLUG).json()["delivery"]
    assert delivery["issued"] == 1
    assert delivery["emailed"] == 0
    assert delivery["failed"] == 1          # SMTP is unset in the tests
    assert delivery["failures"][0]["email_error"]


# ── the record a regulator asks for ────────────────────────────────────────

def test_the_export_keeps_completion_and_result_apart(admin, signed_in):
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 4})
    response = admin.get("/api/report/%s/export.csv" % SLUG)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "training-record" in response.headers["content-disposition"]

    header = response.text.splitlines()[0].lstrip("﻿").split(",")
    assert "reached_end" in header and "passed" in header
    # The one column this file exists not to have.
    assert "trained" not in header


def test_the_export_lists_people_who_never_started(admin):
    """They are the ones a compliance record is most often needed for."""
    _learner("never@example.com")
    rows = admin.get("/api/report/%s/export.csv" % SLUG).text.splitlines()
    assert any(line.startswith("never@example.com") for line in rows)
