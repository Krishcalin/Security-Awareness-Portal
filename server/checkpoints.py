"""Two questions every five slides, to find out whether anybody is still there.

A narrated course can be left playing to an empty chair. Reaching the last
slide proves the audio finished, which is precisely the thing this product
already refuses to call training. A checkpoint is the smallest honest test that
somebody is present: after every fifth slide, two questions about what has just
been covered, answered before the course goes on.

IT IS NOT AN EXAM. A wrong answer shows the right one and the explanation and
then continues. The graded check at the end is where a pass is earned; a
checkpoint that could fail somebody would turn thirty-one slides into six
exams, and the first thing anybody would do is stop answering honestly. What it
does produce is evidence — six pairs of answers spread through the course say
something about attention that a completion flag cannot.

A CHECKPOINT QUESTION IS SPENT. This module's whole reason for showing the
answer and the explanation is to teach; the consequence is that the question can
never appear in that learner's graded check, because they have been shown the
answer. `api.py` opens by saying the answer is never sent before it is earned,
and a checkpoint that leaked into the final ten would make the score measure who
had seen the question rather than who knew the material. `seen_question_ids`
exists for exactly that exclusion, and `_begin_attempt` applies it. The
arithmetic is comfortable: a bank of a hundred, twelve spent on checkpoints,
ten dealt from the eighty-eight that remain.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Dict, List, Optional, Sequence

from server import content, cycles, db

#: A checkpoint after every fifth slide.
EVERY = 5

#: How many questions each one asks. Two is enough to distinguish somebody
#: reading from somebody clicking, and few enough that six of them do not turn
#: a fifty-minute course into an hour of quizzing.
QUESTIONS = 2

_DRAW = secrets.SystemRandom()


def points_for(lesson_count: int) -> List[int]:
    """The slides a checkpoint follows: 5, 10, 15 ...

    NEVER AFTER THE LAST SLIDE. A checkpoint there is the knowledge check, and
    asking two ungraded questions immediately before the graded ten would be
    both a duplicate and an odd thing to do to somebody about to sit an exam.
    On this course slide 31 is the quiz prompt itself, so the rule and the
    content agree; the rule is written to hold either way.
    """
    return [n for n in range(EVERY, lesson_count + 1, EVERY) if n < lesson_count]


def band(after_ordinal: int) -> range:
    """The slides a checkpoint may ask about: the five it follows.

    Only the five, not everything so far. The point is to ask about what was
    just said — a question about slide two, twenty-five slides later, tests
    memory rather than attention, and the graded check already does that.
    """
    return range(max(1, after_ordinal - EVERY + 1), after_ordinal + 1)


def seen_question_ids(learner_id: int, module_id: int,
                      cycle_id: Optional[int]) -> List[int]:
    """Questions a checkpoint has already shown this learner, this cycle.

    Read by the graded check to exclude them. Includes questions that were
    dealt but not answered: the learner saw the prompt, and although they were
    not shown the answer, dealing the same one again in the check is still a
    question they have had longer to think about than the other nine.
    """
    return [row["question_id"] for row in db.query(
        "SELECT question_id FROM checkpoint_answer "
        "WHERE learner_id = %s AND module_id = %s "
        "  AND cycle_id IS NOT DISTINCT FROM %s",
        (learner_id, module_id, cycle_id))]


def _dealt(learner_id: int, module_id: int, cycle_id: Optional[int],
           after_ordinal: int) -> List[Dict[str, Any]]:
    return db.query(
        """
        SELECT c.position, c.chosen_index, c.correct, c.answered_at,
               q.id, q.ordinal, q.prompt, q.options, q.correct_index,
               q.explains, q.teaches
        FROM checkpoint_answer c JOIN question q ON q.id = c.question_id
        WHERE c.learner_id = %s AND c.module_id = %s
          AND c.cycle_id IS NOT DISTINCT FROM %s AND c.after_ordinal = %s
        ORDER BY c.position
        """, (learner_id, module_id, cycle_id, after_ordinal))


def _bank(module_id: int, after_ordinal: int,
          exclude: Sequence[int]) -> List[Dict[str, Any]]:
    """Candidates for this checkpoint: questions about the five slides it
    follows, minus anything a checkpoint has already used this cycle."""
    return db.query(
        """
        SELECT id, ordinal, prompt, options, correct_index, explains, teaches
        FROM question
        WHERE module_id = %s AND teaches = ANY(%s) AND NOT (id = ANY(%s))
        """, (module_id, list(band(after_ordinal)), list(exclude)))


def deal(learner_id: int, module_id: int, after_ordinal: int
         ) -> List[Dict[str, Any]]:
    """This learner's questions for this checkpoint, dealing them if needed.

    Idempotent by construction: a learner who reloads gets the same two. A
    fresh draw on every request would let somebody reload past a question they
    did not like until an easier pair arrived, which is the same failure the
    graded check avoids by writing its hand down in `attempt_question`.

    THE GUARANTEE IS THE UNIQUE INDEX, not the early return below. Deleting the
    early return changes nothing: the insert conflicts on
    `checkpoint_answer_slot_idx`, does nothing, and the read-back returns the
    pair that was already stored. The early return is there to save the round
    trip, and the constraint is there because two tabs can reach this at once.
    """
    cycle_id = cycles.current_id(module_id)
    existing = _dealt(learner_id, module_id, cycle_id, after_ordinal)
    if existing:
        return existing

    # The exclusion is belt-and-braces TODAY and is kept deliberately.
    # Consecutive bands are disjoint — [1..5], [6..10] — so two checkpoints
    # cannot draw the same question from `teaches` alone, and that is what a
    # mutation of this line proves by surviving. It stops being true the moment
    # a question's `teaches` is re-authored between two deals in the same
    # cycle, which a long-running cycle and a content republish make ordinary
    # rather than exotic.
    candidates = _bank(module_id, after_ordinal,
                       seen_question_ids(learner_id, module_id, cycle_id))
    if not candidates:
        # Re-authored content can leave a band with nothing to ask. A
        # checkpoint with no questions is not an error and must not become a
        # locked door: the caller reads the empty list as "nothing to ask
        # here" and lets the learner past.
        return []

    drawn = _DRAW.sample(candidates, min(QUESTIONS, len(candidates)))
    with db.connection() as conn:
        for position, question in enumerate(drawn):
            conn.execute(
                """
                INSERT INTO checkpoint_answer
                    (learner_id, module_id, cycle_id, after_ordinal, position,
                     question_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (learner_id, module_id, cycle_id, after_ordinal, position,
                 question["id"]))
        conn.commit()
    # Read back rather than returning `drawn`: two tabs opening the same
    # checkpoint at once both insert, one loses to ON CONFLICT, and the loser
    # must show the hand that was actually stored rather than its own.
    return _dealt(learner_id, module_id, cycle_id, after_ordinal)


def state(learner_id: int, module_id: int, after_ordinal: int) -> Dict[str, Any]:
    """The checkpoint as the browser is allowed to see it.

    Every question goes through `content.question_for_learner`, so the answer
    is absent by construction rather than by being deleted here. An already
    answered question comes back with its reveal attached, so a reload after
    answering shows the explanation again instead of a blank form.
    """
    rows = deal(learner_id, module_id, after_ordinal)
    questions = []
    for row in rows:
        item = dict(content.question_for_learner(row))
        item["position"] = row["position"]
        if row["answered_at"] is not None:
            item["answered"] = {
                "chosen_index": row["chosen_index"],
                **content.reveal(row, row["chosen_index"]),
            }
        questions.append(item)
    return {
        "after_ordinal": after_ordinal,
        "questions": questions,
        # True only when every question has been answered. The player uses it
        # to decide whether the way forward is open, so "no questions to ask"
        # has to read as complete rather than as permanently blocked.
        "complete": all(q.get("answered") for q in questions),
    }


class AlreadyAnswered(Exception):
    """One answer per question per checkpoint, for the reason the graded check
    gives: re-answering until the tick appears makes the record meaningless."""


class NoSuchQuestion(Exception):
    pass


def answer(learner_id: int, module_id: int, after_ordinal: int, position: int,
           chosen_index: int, took_ms: Optional[int] = None) -> Dict[str, Any]:
    """Grade one checkpoint question and reveal it.

    Grading happens here rather than in the browser for the same reason it does
    for the graded check: an answer the client checks is an answer the client
    can be told to accept.
    """
    cycle_id = cycles.current_id(module_id)
    rows = {row["position"]: row
            for row in _dealt(learner_id, module_id, cycle_id, after_ordinal)}
    row = rows.get(position)
    if row is None:
        raise NoSuchQuestion("no question at position %d of the checkpoint "
                             "after slide %d" % (position, after_ordinal))
    if row["answered_at"] is not None:
        raise AlreadyAnswered("already answered")

    options = row["options"] or []
    if not 0 <= chosen_index < len(options):
        raise NoSuchQuestion("that option does not exist")

    if took_ms is not None:
        took_ms = max(0, min(took_ms, 1000 * 60 * 60))

    outcome = content.reveal(row, chosen_index)
    db.execute(
        """
        UPDATE checkpoint_answer
           SET chosen_index = %s, correct = %s, took_ms = %s, asked = %s,
               answered_at = now()
         WHERE learner_id = %s AND module_id = %s
           AND cycle_id IS NOT DISTINCT FROM %s
           AND after_ordinal = %s AND position = %s
        """,
        (chosen_index, outcome["correct"], took_ms,
         json.dumps({"prompt": row["prompt"], "options": options,
                     "correct_index": row["correct_index"]}),
         learner_id, module_id, cycle_id, after_ordinal, position))
    return {"position": position, "chosen_index": chosen_index, **outcome}


def progress(learner_id: int, module_id: int) -> Dict[int, bool]:
    """Which checkpoints this learner has finished, this cycle.

    Used by resume: somebody who left in the middle of a checkpoint comes back
    to it rather than to the slide after it.
    """
    rows = db.query(
        """
        SELECT after_ordinal,
               bool_and(answered_at IS NOT NULL) AS done
        FROM checkpoint_answer
        WHERE learner_id = %s AND module_id = %s
          AND cycle_id IS NOT DISTINCT FROM %s
        GROUP BY after_ordinal
        """, (learner_id, module_id, cycles.current_id(module_id)))
    return {row["after_ordinal"]: row["done"] for row in rows}
