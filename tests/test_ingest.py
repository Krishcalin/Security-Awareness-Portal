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

    # A question this attempt was actually DEALT — the bank is a hundred and
    # only ten are asked, so the last one in the file is usually not among
    # them, and answering it would be refused.
    dealt = started["questions"][0]["ordinal"]
    answered = next(q for q in payload["questions"] if q["ordinal"] == dealt)
    restore_content.post("/api/attempts/%d/responses" % started["attempt_id"],
                         json={"ordinal": dealt, "chosen_index": 0})
    assert db.one("SELECT count(*) c FROM response")["c"] == 1

    payload["questions"] = [q for q in payload["questions"]
                            if q["ordinal"] != dealt]
    ingest.sync([payload])

    kept = db.one("SELECT asked, chosen_index FROM response")
    assert kept is not None, "re-authoring the content destroyed the answer"
    assert kept["asked"]["prompt"] == answered["prompt"]


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
    # Ten drawn from what is left, not the whole bank.
    from server.config import settings
    assert started["out_of"] == min(settings.quiz_length,
                                    len(payload["questions"]))


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


# ── recorded narration ─────────────────────────────────────────────────────
#
# Which recordings a deployment holds is not part of the course, so it is
# resolved here rather than baked into the built content.

def test_a_recording_is_attached_only_while_it_is_of_the_current_script(
        tmp_path, monkeypatch):
    """THE guard. An edited script with an unregenerated recording means a
    learner hears one wording while reading another on screen — worse than a
    robotic voice, and invisible to whoever made the edit. So the recording is
    dropped and the slide falls back to the synthesiser: a worse voice rather
    than a wrong one."""
    import json
    from server import db, ingest
    from tools import build_narration

    payload = _authored()
    slide = payload["lessons"][0]
    slug = payload["slug"]

    folder = tmp_path / slug
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"not really audio")
    (folder / "01.timings.json").write_text("[]", encoding="utf-8")

    def manifest_for(text):
        (folder / "manifest.json").write_text(json.dumps({"slides": {
            str(slide["ordinal"]): {
                "file": "01.mp3", "timings": "01.timings.json",
                "script_sha": build_narration.script_hash(text),
                "seconds": 12.0, "words": 4,
            }}}), encoding="utf-8")

    monkeypatch.setattr(ingest, "NARRATION", tmp_path)

    manifest_for(slide["narration"])            # recorded from these words
    ingest.sync([payload])
    row = db.one("SELECT audio_url, narration_seconds FROM lesson "
                 "WHERE ordinal = %s", (slide["ordinal"],))
    assert row["audio_url"].endswith("01.mp3")
    assert row["narration_seconds"] == 12       # the recording's own length

    manifest_for("something else entirely")     # the script has moved on
    ingest.sync([payload])
    row = db.one("SELECT audio_url, narration_seconds FROM lesson "
                 "WHERE ordinal = %s", (slide["ordinal"],))
    assert row["audio_url"] == "", "a stale recording was left attached"
    assert row["narration_seconds"] == slide["narration_seconds"]


def test_a_recording_whose_file_has_gone_is_not_offered(tmp_path, monkeypatch):
    """An audio_url pointing at nothing is recoverable in the player, but not
    something to rely on the player to survive."""
    import json
    from server import ingest
    payload = _authored()
    folder = tmp_path / payload["slug"]
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"slides": {"1": {
        "file": "01.mp3", "timings": "", "script_sha": "whatever",
        "seconds": 12.0, "words": 4,
    }}}), encoding="utf-8")
    monkeypatch.setattr(ingest, "NARRATION", tmp_path)
    assert ingest.recordings(payload["slug"]) == {}


def test_a_deployment_with_no_recordings_falls_back_rather_than_failing(
        tmp_path, monkeypatch):
    """A clean checkout has the course and none of the audio."""
    from server import db, ingest
    payload = _authored()
    monkeypatch.setattr(ingest, "NARRATION", tmp_path / "nothing-here")
    summary = ingest.sync([payload])
    assert summary[0]["recorded"] == 0
    assert db.one("SELECT count(*) c FROM lesson WHERE audio_url <> ''")["c"] == 0
    # And every slide still has words for the browser to read.
    assert db.one("SELECT count(*) c FROM lesson WHERE narration <> ''")["c"] > 0


def test_a_word_track_is_never_attached_without_its_recording(restore_content, tmp_path,
                                                              monkeypatch):
    from server import db, ingest
    payload = _authored()
    monkeypatch.setattr(ingest, "NARRATION", tmp_path / "nothing-here")
    ingest.sync([payload])
    assert db.one("SELECT count(*) c FROM lesson "
                  "WHERE audio_timings_url <> '' AND audio_url = ''")["c"] == 0


def test_the_two_definitions_of_the_same_words_agree():
    """`build_narration` writes a recording's provenance and `ingest` checks
    it. Two different hashes would silently disagree for ever."""
    from server import ingest
    from tools import build_narration
    for text in ("One thing.", "Line one.\nLine two.", "  padded  "):
        assert ingest.script_hash(text) == build_narration.script_hash(text)


def test_rewrapping_a_paragraph_does_not_throw_away_a_good_recording():
    from tools import build_narration
    assert build_narration.script_hash("One thing.  Then\nanother.") == \
        build_narration.script_hash("One thing. Then another.")


def test_the_module_length_is_what_it_actually_takes(restore_content, tmp_path, monkeypatch):
    """A recording is rarely the length the word count predicted, and the
    figure shown before somebody presses play should describe what they are
    about to hear."""
    from server import db, ingest
    payload = _authored()
    monkeypatch.setattr(ingest, "NARRATION", tmp_path / "nothing-here")
    ingest.sync([payload])
    seconds = db.one("SELECT COALESCE(sum(narration_seconds), 0) s "
                     "FROM lesson")["s"]
    assert db.one("SELECT minutes FROM module")["minutes"] == round(seconds / 60)
