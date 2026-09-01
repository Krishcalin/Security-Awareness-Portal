"""Two questions every five slides, to find out whether anybody is still there.

WHY THIS EXISTS. A narrated course can be left playing to an empty chair, and
reaching the last slide then proves only that the audio finished — the exact
thing this product refuses to call training. A checkpoint after every fifth
slide asks two questions about what was just covered, and the course does not
go on until they are answered.

THE RULE THAT COSTS THE MOST TO GET WRONG. A checkpoint shows the correct
answer and the explanation; that is what it is for. So a question a checkpoint
has used can never appear in that learner's graded check, or the score measures
who was shown the answer rather than who knew it. `server/api.py` opens by
saying the answer is never sent before it is earned; leaking it forwards is the
same failure from the other direction.

The rest are the rules that keep it a checkpoint rather than an exam: a wrong
answer teaches and lets you past, a reload does not deal a fresh pair, and an
answer cannot be given twice.
"""
from __future__ import annotations

import os
import uuid

import pytest

from tests.conftest import needs_db

pytestmark = needs_db


def _module():
    from server import db
    return db.one("SELECT id, slug FROM module ORDER BY id LIMIT 1")


def _learner(email=None):
    from server import auth
    return auth.upsert_learner(
        entra_oid=str(uuid.uuid4()),
        email=email or "cp-%s@example.com" % os.urandom(4).hex(),
        upn="", display_name="Checkpoint Person", department="Ops")


def _answer_all(learner_id, module_id, after, correctly=True):
    from server import checkpoints
    out = []
    for row in checkpoints.deal(learner_id, module_id, after):
        right = row["correct_index"]
        chosen = right if correctly else (right + 1) % len(row["options"])
        out.append(checkpoints.answer(learner_id, module_id, after,
                                      row["position"], chosen))
    return out


# ── where they fall ──────────────────────────────────────────────────────────

def test_a_checkpoint_follows_every_fifth_slide():
    from server import checkpoints
    assert checkpoints.points_for(31) == [5, 10, 15, 20, 25, 30]


def test_there_is_never_one_after_the_last_slide():
    """A checkpoint there is the knowledge check. Two ungraded questions
    immediately before the graded ten would be a duplicate and an odd thing to
    do to somebody about to sit an exam."""
    from server import checkpoints
    assert checkpoints.points_for(30) == [5, 10, 15, 20, 25]
    assert checkpoints.points_for(10) == [5]
    assert checkpoints.points_for(5) == []
    assert checkpoints.points_for(3) == []


def test_a_checkpoint_asks_only_about_the_five_slides_it_follows():
    """A question about slide two, twenty-five slides later, tests memory
    rather than attention — and the graded check already does that."""
    from server import checkpoints
    assert list(checkpoints.band(5)) == [1, 2, 3, 4, 5]
    assert list(checkpoints.band(20)) == [16, 17, 18, 19, 20]


def test_the_questions_come_from_the_slides_just_covered(clean):
    from server import checkpoints
    module = _module()
    learner = _learner()
    for after in (5, 20):
        dealt = checkpoints.deal(learner["id"], module["id"], after)
        assert len(dealt) == checkpoints.QUESTIONS
        for row in dealt:
            assert row["teaches"] in checkpoints.band(after), \
                "a checkpoint asked about a slide it does not follow"


# ── the rule that protects the graded check ──────────────────────────────────

def test_a_question_a_checkpoint_used_is_kept_out_of_the_graded_check(clean):
    """THE ONE THAT MATTERS. Otherwise the check scores who was shown the
    answer during the course."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    for after in checkpoints.points_for(31):
        _answer_all(learner["id"], module["id"], after)

    spent = set(checkpoints.seen_question_ids(
        learner["id"], module["id"], None))
    assert len(spent) == 12, "six checkpoints of two questions"

    clean.cookies.clear()
    from server import auth
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(learner["entra_oid"]))
    started = clean.post("/api/modules/%s/attempts" % module["slug"])
    assert started.status_code == 200, started.text
    # By ORDINAL: the allowlist that strips the answer also withholds the
    # question id, so the browser never learns it and neither does this test.
    from server import db
    spent_ordinals = {row["ordinal"] for row in db.query(
        "SELECT ordinal FROM question WHERE id = ANY(%s)", (list(spent),))}
    dealt = {q["ordinal"] for q in started.json()["questions"]}
    assert not (dealt & spent_ordinals), (
        "the check dealt a question the learner was already shown the "
        "answer to")
    assert len(dealt) == 10, "the check still deals a full hand"


def test_a_question_dealt_but_not_answered_is_also_spent(clean):
    """They saw the prompt. Dealing it again in the check gives them one
    question they have had twenty minutes to think about and nine they have
    not."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    dealt = checkpoints.deal(learner["id"], module["id"], 5)
    spent = checkpoints.seen_question_ids(learner["id"], module["id"], None)
    assert {row["id"] for row in dealt} == set(spent)


def test_two_checkpoints_never_ask_the_same_question(clean):
    from server import checkpoints
    module = _module()
    learner = _learner()
    seen = []
    for after in checkpoints.points_for(31):
        seen += [row["id"] for row in
                 checkpoints.deal(learner["id"], module["id"], after)]
    assert len(seen) == len(set(seen)), "a question was asked twice"


# ── it teaches, it does not mark ─────────────────────────────────────────────

def test_a_wrong_answer_reveals_the_right_one_with_its_explanation(clean):
    """A quiz that says only "incorrect" teaches nothing. The explanation is
    the part that does the work, and it is shown either way."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    outcome = _answer_all(learner["id"], module["id"], 5, correctly=False)[0]
    assert outcome["correct"] is False
    assert isinstance(outcome["correct_index"], int)
    assert outcome["explains"], "no explanation was offered"
    # And which slide it came from, so the player can offer it again.
    assert outcome["teaches"] in checkpoints.band(5)


def test_a_right_answer_is_revealed_the_same_way(clean):
    from server import checkpoints
    module = _module()
    learner = _learner()
    outcome = _answer_all(learner["id"], module["id"], 5)[0]
    assert outcome["correct"] is True
    assert outcome["explains"]


def test_getting_them_wrong_still_completes_the_checkpoint(clean):
    """It is not an exam. Six checkpoints that could each fail somebody would
    turn a course into six exams, and the first thing anybody would do is stop
    answering honestly."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    _answer_all(learner["id"], module["id"], 5, correctly=False)
    assert checkpoints.state(learner["id"], module["id"], 5)["complete"] is True


# ── it cannot be gamed ───────────────────────────────────────────────────────

def test_reloading_does_not_deal_a_fresh_pair(clean):
    """Otherwise somebody reloads past a question they do not like until an
    easier pair arrives — the failure `attempt_question` exists to prevent for
    the graded check."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    first = [row["id"] for row in checkpoints.deal(learner["id"], module["id"], 10)]
    for _ in range(5):
        again = [row["id"] for row
                 in checkpoints.deal(learner["id"], module["id"], 10)]
        assert again == first


def test_a_question_cannot_be_answered_twice(clean):
    """Re-answering until the tick appears makes the record meaningless, which
    is the same reason the graded check refuses it."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    row = checkpoints.deal(learner["id"], module["id"], 5)[0]
    checkpoints.answer(learner["id"], module["id"], 5, 0, row["correct_index"])
    with pytest.raises(checkpoints.AlreadyAnswered):
        checkpoints.answer(learner["id"], module["id"], 5, 0, 0)


def test_an_option_that_does_not_exist_is_refused(clean):
    from server import checkpoints
    module = _module()
    learner = _learner()
    checkpoints.deal(learner["id"], module["id"], 5)
    with pytest.raises(checkpoints.NoSuchQuestion):
        checkpoints.answer(learner["id"], module["id"], 5, 0, 99)


def test_answering_a_position_that_was_never_dealt_is_refused(clean):
    from server import checkpoints
    module = _module()
    learner = _learner()
    checkpoints.deal(learner["id"], module["id"], 5)
    with pytest.raises(checkpoints.NoSuchQuestion):
        checkpoints.answer(learner["id"], module["id"], 5, 7, 0)


# ── what the browser is allowed to see ───────────────────────────────────────

def test_the_answer_is_not_in_the_payload_before_it_is_earned(clean):
    """By construction, through the same allowlist the graded check uses."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    state = checkpoints.state(learner["id"], module["id"], 5)
    for question in state["questions"]:
        assert "correct_index" not in question
        assert "explains" not in question
    assert state["complete"] is False


def test_after_answering_the_explanation_comes_back_on_a_reload(clean):
    """Somebody who refreshes mid-checkpoint should see what they were told,
    not a blank form they cannot fill in again."""
    from server import checkpoints
    module = _module()
    learner = _learner()
    _answer_all(learner["id"], module["id"], 5)
    state = checkpoints.state(learner["id"], module["id"], 5)
    assert state["complete"] is True
    for question in state["questions"]:
        assert question["answered"]["explains"]
        assert isinstance(question["answered"]["correct_index"], int)


# ── over HTTP ────────────────────────────────────────────────────────────────

def test_the_endpoint_serves_and_grades_a_checkpoint(signed_in):
    module = _module()
    got = signed_in.get("/api/modules/%s/checkpoints/5" % module["slug"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["after_ordinal"] == 5
    assert len(body["questions"]) == 2
    assert body["complete"] is False
    assert "correct_index" not in body["questions"][0]

    posted = signed_in.post(
        "/api/modules/%s/checkpoints/5/answers" % module["slug"],
        json={"position": 0, "chosen_index": 0})
    assert posted.status_code == 200, posted.text
    assert "explains" in posted.json()

    again = signed_in.post(
        "/api/modules/%s/checkpoints/5/answers" % module["slug"],
        json={"position": 0, "chosen_index": 1})
    assert again.status_code == 409


def test_a_slide_no_checkpoint_follows_is_a_404(signed_in):
    """Not an empty checkpoint. An empty one reads as "nothing to answer here"
    and would let a client manufacture a way past slide 5 by asking about
    slide 7."""
    module = _module()
    for ordinal in (1, 7, 31, 999):
        got = signed_in.get(
            "/api/modules/%s/checkpoints/%d" % (module["slug"], ordinal))
        assert got.status_code == 404, ordinal


def test_a_stranger_cannot_read_a_checkpoint(clean):
    module = _module()
    got = clean.get("/api/modules/%s/checkpoints/5" % module["slug"])
    assert got.status_code == 401


# ── somebody has to read what they found ─────────────────────────────────────

def test_the_report_says_what_the_checkpoints_found(clean):
    """Evidence collected and read by nobody is the failure this codebase keeps
    finding. The checkpoints are the only measure of attention DURING the
    course; everything else on the report describes the check at the end."""
    from server import checkpoints, reporting
    module = _module()
    learner = _learner()
    _answer_all(learner["id"], module["id"], 5, correctly=True)
    _answer_all(learner["id"], module["id"], 10, correctly=False)

    figures = reporting.checkpoint_attention(module["id"])
    assert figures["answered"] == 4
    assert figures["correct"] == 2
    assert figures["people"] == 1
    assert [s["after_ordinal"] for s in figures["stops"]] == [5, 10]
    assert figures["stops"][0]["correct"] == 2
    assert figures["stops"][1]["correct"] == 0


def test_no_proportion_is_quoted_from_a_handful_of_answers(clean):
    """The same floor the question statistics use. "100% correct" from four
    answers is noise wearing the costume of a statistic."""
    from server import reporting
    module = _module()
    learner = _learner()
    _answer_all(learner["id"], module["id"], 5)

    figures = reporting.checkpoint_attention(module["id"])
    assert figures["answered"] == 2
    assert figures["correct_rate"] is None
    assert figures["stops"][0]["correct_rate"] is None


def test_a_dealt_but_unanswered_question_is_not_counted_as_wrong(clean):
    """Null is not "wrong". A checkpoint reached and not yet answered is a
    different state from one somebody got wrong, and reading the first as the
    second would make an abandoned course look like an inattentive learner."""
    from server import checkpoints, reporting
    module = _module()
    learner = _learner()
    checkpoints.deal(learner["id"], module["id"], 5)

    figures = reporting.checkpoint_attention(module["id"])
    assert figures["answered"] == 0
    assert figures["correct"] == 0
    assert figures["people"] == 0


def test_the_report_endpoint_carries_the_attention_figures(signed_in):
    from server import auth, db
    db.execute("UPDATE learner SET role = 'admin' WHERE id = %s",
               (signed_in.learner["id"],))
    module = _module()
    got = signed_in.get("/api/report/%s" % module["slug"])
    assert got.status_code == 200, got.text
    assert "attention" in got.json(), \
        "the checkpoints record evidence the report never shows"


def test_consecutive_checkpoints_cover_disjoint_slides(clean):
    """The reason two checkpoints cannot ask the same question, stated
    directly. `deal` also filters what has been seen, but a mutation of that
    filter survives — because THIS is what actually holds the guarantee, and a
    test should pin the mechanism rather than the belt over it."""
    from server import checkpoints
    stops = checkpoints.points_for(31)
    covered = [set(checkpoints.band(n)) for n in stops]
    for earlier, later in zip(covered, covered[1:]):
        assert not (earlier & later)
