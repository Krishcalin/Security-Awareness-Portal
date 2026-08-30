"""The API, against a real database.

The tests that matter most here are the ones about what the API refuses. A
portal that reports 94% trained is trusted by people making decisions about
where to spend a security budget, so the ways this can quietly report more
than it knows are the ways it fails:

  - sending the answers to the browser, which makes every score meaningless
  - letting one question be answered until it goes green, which makes a score
    a measure of persistence
  - losing which attempt a pass came from, which makes the third go look like
    the first
  - dropping unanswered questions from the denominator, which rewards skipping
    the ones you do not know
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import needs_db

pytestmark = needs_db

SLUG = "security-awareness-essentials"


# ── who is asking ──────────────────────────────────────────────────────────

def test_health_needs_no_session(app_client):
    assert app_client.get("/api/health").json() == {"ok": True}


def test_everything_else_needs_a_session(clean):
    for method, path in (("get", "/api/me"),
                         ("get", "/api/modules"),
                         ("get", "/api/modules/" + SLUG),
                         ("post", "/api/modules/%s/attempts" % SLUG)):
        response = getattr(clean, method)(path)
        assert response.status_code == 401, path


def test_a_tampered_cookie_is_not_a_session(clean):
    from server import auth
    token = auth.issue("some-oid")
    body, _, signature = token.rpartition(".")
    clean.cookies.set(auth.COOKIE_NAME, body + "." + signature[::-1])
    assert clean.get("/api/me").status_code == 401


def test_an_expired_cookie_is_not_a_session(clean):
    import time
    from server import auth
    old = auth.issue("some-oid", now=time.time() - auth.MAX_AGE_SECONDS - 60)
    clean.cookies.set(auth.COOKIE_NAME, old)
    assert clean.get("/api/me").status_code == 401


def test_a_session_for_a_deleted_learner_is_not_a_session(signed_in):
    """A leaver whose row is gone, or a restored backup."""
    from server import db
    assert signed_in.get("/api/me").status_code == 200
    db.execute("DELETE FROM learner")
    assert signed_in.get("/api/me").status_code == 401


def test_me_returns_the_signed_in_person(signed_in):
    body = signed_in.get("/api/me").json()
    assert body["email"] == "test@example.com"
    assert body["department"] == "Finance"


# ── the answers must not leave the server ──────────────────────────────────

def test_module_detail_carries_no_questions(signed_in):
    body = signed_in.get("/api/modules/" + SLUG).json()
    assert body["question_count"] > 0
    assert "questions" not in body
    assert "correct_index" not in json.dumps(body)


def test_questions_reach_the_browser_without_their_answers(signed_in):
    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert started["questions"], "the attempt handed back no questions"
    for question in started["questions"]:
        assert set(question) == {"ordinal", "prompt", "options"}
    raw = json.dumps(started)
    assert "correct_index" not in raw
    assert "explains" not in raw


def test_the_authored_explanations_are_not_in_the_payload(signed_in):
    """Not just the key — the text itself. An explanation says which answer is
    right in plain English, so leaking it leaks the answer."""
    from server import db
    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    raw = json.dumps(started)
    for row in db.query("SELECT explains FROM question WHERE explains <> ''"):
        assert row["explains"] not in raw


# ── answering ──────────────────────────────────────────────────────────────

def _first_question(client):
    started = client.post("/api/modules/%s/attempts" % SLUG).json()
    return started, started["questions"][0]


def test_answering_grades_server_side_and_explains(signed_in):
    from server import db
    started, question = _first_question(signed_in)
    correct = db.one("SELECT q.correct_index FROM question q JOIN module m "
                     "ON m.id = q.module_id WHERE m.slug = %s AND q.ordinal = %s",
                     (SLUG, question["ordinal"]))["correct_index"]
    body = signed_in.post(
        "/api/attempts/%d/responses" % started["attempt_id"],
        json={"ordinal": question["ordinal"], "chosen_index": correct,
              "took_ms": 4200}).json()
    assert body["correct"] is True
    assert body["correct_index"] == correct
    assert body["explains"]
    assert body["teaches"]


def test_a_wrong_answer_is_explained_too(signed_in):
    from server import db
    started, question = _first_question(signed_in)
    correct = db.one("SELECT q.correct_index FROM question q JOIN module m "
                     "ON m.id = q.module_id WHERE m.slug = %s AND q.ordinal = %s",
                     (SLUG, question["ordinal"]))["correct_index"]
    wrong = (correct + 1) % len(question["options"])
    body = signed_in.post(
        "/api/attempts/%d/responses" % started["attempt_id"],
        json={"ordinal": question["ordinal"], "chosen_index": wrong}).json()
    assert body["correct"] is False
    assert body["explains"], "being told only 'incorrect' teaches nothing"


def test_a_question_cannot_be_answered_twice_in_one_attempt(signed_in):
    """Otherwise the score measures persistence, not knowledge."""
    started, question = _first_question(signed_in)
    url = "/api/attempts/%d/responses" % started["attempt_id"]
    payload = {"ordinal": question["ordinal"], "chosen_index": 0}
    assert signed_in.post(url, json=payload).status_code == 200
    second = signed_in.post(url, json=payload)
    assert second.status_code == 409
    assert "new attempt" in second.json()["detail"]


def test_an_option_that_does_not_exist_is_refused(signed_in):
    started, question = _first_question(signed_in)
    response = signed_in.post(
        "/api/attempts/%d/responses" % started["attempt_id"],
        json={"ordinal": question["ordinal"], "chosen_index": 99})
    assert response.status_code == 422


def test_answering_records_what_was_asked(signed_in):
    """So a result stays interpretable after the content is re-authored."""
    from server import db
    started, question = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                   json={"ordinal": question["ordinal"], "chosen_index": 0})
    asked = db.one("SELECT asked FROM response LIMIT 1")["asked"]
    assert asked["prompt"] == question["prompt"]
    assert asked["options"] == question["options"]


def test_someone_elses_attempt_is_not_found(clean):
    from server import auth
    import uuid
    first_oid, second_oid = str(uuid.uuid4()), str(uuid.uuid4())
    auth.upsert_learner(entra_oid=first_oid, email="one@example.com")
    auth.upsert_learner(entra_oid=second_oid, email="two@example.com")

    clean.cookies.set(auth.COOKIE_NAME, auth.issue(first_oid))
    attempt_id = clean.post("/api/modules/%s/attempts" % SLUG).json()["attempt_id"]

    clean.cookies.set(auth.COOKIE_NAME, auth.issue(second_oid))
    stolen = clean.post("/api/attempts/%d/responses" % attempt_id,
                        json={"ordinal": 1, "chosen_index": 0})
    # 404 rather than 403: a 403 confirms the attempt exists.
    assert stolen.status_code == 404


# ── attempts and scoring ───────────────────────────────────────────────────

def test_reopening_resumes_rather_than_starting_attempt_two(signed_in):
    """Otherwise attempt_no measures closed tabs, not retakes."""
    first = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    second = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert first["attempt_id"] == second["attempt_id"]
    assert second["attempt_no"] == 1


def test_a_resumed_attempt_does_not_re_serve_answered_questions(signed_in):
    started, question = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                   json={"ordinal": question["ordinal"], "chosen_index": 0})
    resumed = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert resumed["answered"] == 1
    assert question["ordinal"] not in [q["ordinal"] for q in resumed["questions"]]


def test_unanswered_questions_count_against_the_score(signed_in):
    """Skipping the ones you do not know must not score better than trying."""
    started, question = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                   json={"ordinal": question["ordinal"], "chosen_index": 0})
    finished = signed_in.post(
        "/api/attempts/%d/finish" % started["attempt_id"]).json()
    assert finished["out_of"] == started["out_of"]
    assert finished["unanswered"] == started["out_of"] - 1
    assert finished["score"] <= 1


def test_a_retake_is_a_numbered_second_attempt(signed_in):
    """A pass on the third go is not the same evidence as a pass on the
    first, so the number is kept and reported."""
    first = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    signed_in.post("/api/attempts/%d/finish" % first["attempt_id"])
    second = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert second["attempt_no"] == 2
    assert second["attempt_id"] != first["attempt_id"]
    result = signed_in.post("/api/attempts/%d/finish" % second["attempt_id"]).json()
    assert result["first_attempt"] is False


def test_a_finished_attempt_takes_no_more_answers(signed_in):
    started, question = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/finish" % started["attempt_id"])
    late = signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                          json={"ordinal": question["ordinal"],
                                "chosen_index": 0})
    assert late.status_code == 409


# ── progress ───────────────────────────────────────────────────────────────

def test_progress_only_moves_forward(signed_in):
    url = "/api/modules/%s/progress" % SLUG
    signed_in.post(url, json={"furthest_ordinal": 8})
    body = signed_in.post(url, json={"furthest_ordinal": 2}).json()
    assert body["furthest_ordinal"] == 8, (
        "scrolling back does not unlearn the slides in between")


def test_module_list_separates_reaching_the_end_from_answering(signed_in):
    """Opening and abandoning on slide three must be visible as that, not
    rounded to 'not completed' alongside people who never opened it."""
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 3})
    row = next(m for m in signed_in.get("/api/modules").json()
               if m["slug"] == SLUG)
    assert row["furthest_ordinal"] == 3
    assert row["completed_at"] is None
    assert row["latest_score"] is None


def test_an_unknown_module_is_a_404(signed_in):
    assert signed_in.get("/api/modules/not-a-module").status_code == 404


# ── serving the app itself ─────────────────────────────────────────────────

def _spa_built() -> bool:
    from server.api import SPA
    return SPA.is_dir()


@pytest.mark.skipif(not _spa_built(),
                    reason="no built SPA here; run `npm run build` in frontend/")
def test_the_catch_all_is_registered_after_the_api(app_client):
    """Starlette matches routes in registration order, so a catch-all declared
    above the API returns the front page for every endpoint — which is exactly
    what it did the first time it was added."""
    from server.api import app
    paths = [route.path for route in app.routes if hasattr(route, "path")]
    assert paths[-1] == "/{path:path}"
    assert paths.index("/{path:path}") > paths.index("/api/me")


@pytest.mark.skipif(not _spa_built(), reason="no built SPA here")
def test_a_deep_link_into_a_course_serves_the_app(app_client):
    """Refreshing on /module/essentials must not 404. That page is a route
    inside the app, not a file on disk."""
    response = app_client.get("/module/security-awareness-essentials")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.skipif(not _spa_built(), reason="no built SPA here")
def test_the_catch_all_does_not_serve_files_outside_the_app(app_client):
    """`path` comes from the URL."""
    escape = app_client.get("/../../server/schema.sql")
    assert "CREATE TABLE" not in escape.text


# ── coming back to where you left ──────────────────────────────────────────

def test_progress_records_where_they_are_as_well_as_how_far(signed_in):
    """Two different numbers. Somebody at slide 18 who scrolls back to 4 and
    closes the tab left at 4, and that is where they resume — while their
    progress is still 18."""
    from server import db
    url = "/api/modules/%s/progress" % SLUG
    signed_in.post(url, json={"furthest_ordinal": 18})
    signed_in.post(url, json={"furthest_ordinal": 4})
    row = db.one("SELECT furthest_ordinal, last_ordinal, last_seen_at "
                 "FROM enrolment")
    assert row["furthest_ordinal"] == 18
    assert row["last_ordinal"] == 4
    assert row["last_seen_at"] is not None


def test_resume_points_at_the_slide_they_left(signed_in):
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 12})
    assert signed_in.get("/api/resume").json()["path"] == \
        "/module/%s?slide=12" % SLUG


def test_resume_points_at_the_questions_when_one_is_half_answered(signed_in):
    """Being dropped back into the deck would lose the answers already
    given, and they cannot be given again in the same attempt."""
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 12})
    started, question = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/responses" % started["attempt_id"],
                   json={"ordinal": question["ordinal"], "chosen_index": 0})
    assert signed_in.get("/api/resume").json()["path"] == \
        "/module/%s/check" % SLUG


def test_resume_is_the_front_page_for_somebody_who_has_not_started(signed_in):
    assert signed_in.get("/api/resume").json()["path"] == "/"


def test_a_finished_module_does_not_drag_them_back_into_it(signed_in):
    signed_in.post("/api/modules/%s/progress" % SLUG,
                   json={"furthest_ordinal": 21})
    started, _ = _first_question(signed_in)
    signed_in.post("/api/attempts/%d/finish" % started["attempt_id"])
    assert signed_in.get("/api/resume").json()["path"] == "/"


# ── ten of a hundred ───────────────────────────────────────────────────────

def test_an_attempt_draws_ten_from_the_bank(signed_in):
    from server import db
    from server.config import settings
    bank = db.one("SELECT count(*) c FROM question WHERE NOT retired")["c"]
    assert bank == 100, "the bank is meant to be a hundred"

    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert started["out_of"] == settings.quiz_length == 10
    assert len(started["questions"]) == 10
    ordinals = [q["ordinal"] for q in started["questions"]]
    assert len(set(ordinals)) == 10, "the same question was dealt twice"


def test_the_draw_is_recorded_so_a_resume_does_not_reshuffle(signed_in):
    """Somebody who closes the tab half way through must come back to the same
    ten in the same order. A fresh draw would strand the answers they already
    gave, which cannot be given again in the same attempt."""
    first = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    again = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert first["attempt_id"] == again["attempt_id"]
    assert [q["ordinal"] for q in first["questions"]] == \
        [q["ordinal"] for q in again["questions"]]


def test_two_learners_are_never_dealt_the_same_ten_in_the_same_order(clean):
    """Asked for explicitly. It is enforced by a unique index rather than left
    to probability, so this checks the mechanism as well as the outcome."""
    import uuid
    from server import auth, db

    hands = []
    for _ in range(12):
        oid = str(uuid.uuid4())
        auth.upsert_learner(entra_oid=oid, email="%s@example.com" % oid[:8])
        clean.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
        started = clean.post("/api/modules/%s/attempts" % SLUG).json()
        hands.append(tuple(q["ordinal"] for q in started["questions"]))

    assert len(set(hands)) == len(hands), "two learners got the same hand"
    # And the mechanism: every attempt carries a fingerprint, and they differ.
    prints = [r["question_set"] for r in db.query(
        "SELECT question_set FROM attempt")]
    assert all(prints) and len(set(prints)) == len(prints)


def test_the_draw_is_not_the_same_questions_every_time(clean):
    """A 'random' draw that always returns the first ten would pass every
    other test here."""
    import uuid
    from server import auth
    seen = set()
    for _ in range(8):
        oid = str(uuid.uuid4())
        auth.upsert_learner(entra_oid=oid, email="%s@example.com" % oid[:8])
        clean.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
        started = clean.post("/api/modules/%s/attempts" % SLUG).json()
        seen |= {q["ordinal"] for q in started["questions"]}
    assert len(seen) > 30, "the draw barely moves: only %d questions ever appeared" % len(seen)


def test_a_retake_is_dealt_a_different_hand(signed_in):
    """Otherwise a retake is the same ten again, and the second score measures
    memory of the answers rather than knowledge of the material."""
    first = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    signed_in.post("/api/attempts/%d/finish" % first["attempt_id"])
    second = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    assert second["attempt_no"] == 2
    assert [q["ordinal"] for q in first["questions"]] != \
        [q["ordinal"] for q in second["questions"]]


def test_a_question_not_dealt_to_this_attempt_cannot_be_answered(signed_in):
    """Ninety of the hundred were never asked. Without this the ordinal is
    just a number a learner can put anything into."""
    started = signed_in.post("/api/modules/%s/attempts" % SLUG).json()
    dealt = {q["ordinal"] for q in started["questions"]}
    not_dealt = next(n for n in range(1, 101) if n not in dealt)

    refused = signed_in.post(
        "/api/attempts/%d/responses" % started["attempt_id"],
        json={"ordinal": not_dealt, "chosen_index": 0})
    assert refused.status_code == 404
    assert "not in this attempt" in refused.json()["detail"]


def test_the_module_list_reports_what_a_learner_answers(signed_in):
    """Not the bank size: the card says "10 questions" and a score reads
    "8 of 10"; reporting 100 would make both of those wrong."""
    row = next(m for m in signed_in.get("/api/modules").json()
               if m["slug"] == SLUG)
    assert row["questions"] == 10
    assert row["bank"] == 100


def test_the_module_page_says_both_numbers(signed_in):
    body = signed_in.get("/api/modules/" + SLUG).json()
    assert body["question_count"] == 10
    assert body["question_bank"] == 100
