"""Loading authored content into the database.

The load runs on every start, so it runs over content people have already been
tested against. The property that matters is that it never destroys the record
of what somebody was asked and how they answered — that record IS the product.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import needs_db

pytestmark = needs_db

SLUG = "security-awareness-essentials"


@pytest.fixture
def restore_content(clean):
    """Let a test load altered content, and put the real content back."""
    yield clean
    from server import ingest
    ingest.sync()


def _authored():
    from server import content
    return content.load_modules()[0]


def test_loading_twice_changes_nothing(clean):
    from server import db, ingest
    before = db.query("SELECT id, ordinal, prompt FROM question ORDER BY ordinal")
    ingest.sync()
    after = db.query("SELECT id, ordinal, prompt FROM question ORDER BY ordinal")
    assert before == after


def test_a_dropped_question_is_retired_not_deleted(restore_content):
    """`response` references `question` with ON DELETE CASCADE, so deleting a
    question that is no longer authored would take every answer anybody ever
    gave it along with it."""
    from server import db, ingest
    payload = _authored()
    dropped = payload["questions"][-1]
    payload["questions"] = payload["questions"][:-1]
    summary = ingest.sync([payload])

    assert summary[0]["retired"] == 1
    row = db.one("SELECT retired FROM question q JOIN module m "
                 "ON m.id = q.module_id WHERE m.slug = %s AND q.ordinal = %s",
                 (SLUG, dropped["ordinal"]))
    assert row is not None, "the question was deleted, not retired"
    assert row["retired"] is True


def test_an_answer_survives_its_question_being_retired(restore_content):
    from server import auth, db, ingest
    import uuid

    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email="answered@example.com")
    restore_content.cookies.set(auth.COOKIE_NAME, auth.issue(oid))

    started = restore_content.post("/api/modules/%s/attempts" % SLUG).json()
    payload = _authored()
    last = payload["questions"][-1]
    restore_content.post("/api/attempts/%d/responses" % started["attempt_id"],
                         json={"ordinal": last["ordinal"], "chosen_index": 0})
    assert db.one("SELECT count(*) c FROM response")["c"] == 1

    payload["questions"] = payload["questions"][:-1]
    ingest.sync([payload])

    kept = db.one("SELECT asked, chosen_index FROM response")
    assert kept is not None, "re-authoring the content destroyed the answer"
    assert kept["asked"]["prompt"] == last["prompt"]


def test_a_retired_question_is_no_longer_asked(restore_content):
    from server import auth, ingest
    import uuid

    payload = _authored()
    dropped = payload["questions"][-1]["ordinal"]
    payload["questions"] = payload["questions"][:-1]
    ingest.sync([payload])

    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email="later@example.com")
    restore_content.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
    started = restore_content.post("/api/modules/%s/attempts" % SLUG).json()

    assert dropped not in [q["ordinal"] for q in started["questions"]]
    assert started["out_of"] == len(payload["questions"])


def test_re_authoring_updates_the_content_hash(restore_content):
    from server import db, ingest
    payload = _authored()
    payload["content_hash"] = "0000000000000000"
    payload["title"] = "Renamed"
    ingest.sync([payload])
    row = db.one("SELECT title, content_hash FROM module WHERE slug = %s",
                 (SLUG,))
    assert row["title"] == "Renamed"
    assert row["content_hash"] == "0000000000000000"


def test_a_hand_edited_module_file_is_refused(tmp_path, monkeypatch):
    """The build produces these files; a hand-edited one that has lost a field
    should say so rather than fail somewhere downstream."""
    from server import content
    from server.config import settings
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"slug": "x", "title": "X"}), encoding="utf-8")
    monkeypatch.setattr(settings, "content_dir", tmp_path)
    with pytest.raises(ValueError) as problem:
        content.load_modules()
    assert "build_content" in str(problem.value)
