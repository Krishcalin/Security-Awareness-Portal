"""The HTTP API.

Two rules shape almost everything here.

THE ANSWER IS NEVER SENT BEFORE IT IS EARNED. Questions leave the server
through `content.question_for_learner`, which rebuilds each one from an
allowlist, so `correct_index` and `explains` are not in the payload to be
found. Grading happens here, not in the browser.

A QUESTION IS ANSWERED ONCE PER ATTEMPT. Letting a learner re-answer until the
tick appears is what turns a knowledge check into a clicking exercise, and the
resulting score is indistinguishable from knowing the material — which is the
one thing this product must not report. The second answer is refused; a retake
is a new attempt, numbered, and the number is kept.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from server import auth, content, db, ingest
from server.config import settings

log = logging.getLogger(__name__)

#: Anything longer than this on one question is a walk away from the desk, not
#: thinking time, and would distort the "answered in under two seconds" signal
#: it sits next to.
MAX_SANE_TOOK_MS = 30 * 60 * 1000


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    db.init_schema()
    for summary in ingest.sync():
        log.info("content loaded: %(slug)s (%(lessons)d lessons, "
                 "%(questions)d questions, %(retired)d retired)", summary)
    yield
    db.close_pool()


app = FastAPI(title="Security Awareness Portal", lifespan=lifespan)


# ── requests ───────────────────────────────────────────────────────────────

class Progress(BaseModel):
    furthest_ordinal: int = Field(ge=0)


class Answer(BaseModel):
    ordinal: int
    chosen_index: Optional[int] = None
    # Client-reported, and that is acceptable HERE because it feeds a judgement
    # about the QUESTION, not about the person: nobody has an incentive to
    # inflate their own count of answers-that-looked-like-guesses. It is not
    # used as a control over anything.
    took_ms: Optional[int] = None


# ── helpers ────────────────────────────────────────────────────────────────

def _module(slug: str) -> Dict[str, Any]:
    row = db.one("SELECT * FROM module WHERE slug = %s AND published", (slug,))
    if not row:
        raise HTTPException(status_code=404, detail="no such module")
    return row


def _questions(module_id: int) -> List[Dict[str, Any]]:
    return db.query(
        "SELECT id, ordinal, prompt, options, correct_index, explains, teaches "
        "FROM question WHERE module_id = %s AND NOT retired ORDER BY ordinal",
        (module_id,))


def _owned_attempt(attempt_id: int, learner: Dict[str, Any]) -> Dict[str, Any]:
    row = db.one("SELECT * FROM attempt WHERE id = %s", (attempt_id,))
    # Same answer for "does not exist" and "is not yours": otherwise the API
    # tells anyone who asks which attempt ids are real.
    if not row or row["learner_id"] != learner["id"]:
        raise HTTPException(status_code=404, detail="no such attempt")
    return row


# ── routes ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/api/me")
def me(learner: Dict[str, Any] = Depends(auth.current_learner)):
    return {"id": learner["id"], "email": learner["email"],
            "display_name": learner["display_name"],
            "department": learner["department"]}


@app.get("/api/modules")
def modules(learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Every published module, with this learner's standing in each.

    `furthest_ordinal` is reported beside `completed_at` rather than folded
    into it, so a module people open and abandon on slide three is visible as
    that, instead of rounding to "not completed" alongside the people who
    never opened it.
    """
    return db.query(
        """
        SELECT m.slug, m.title, m.summary, m.minutes, m.topic,
               m.content_hash,
               (SELECT count(*) FROM lesson l WHERE l.module_id = m.id)
                   AS lessons,
               (SELECT count(*) FROM question q
                 WHERE q.module_id = m.id AND NOT q.retired) AS questions,
               e.started_at, e.completed_at,
               COALESCE(e.furthest_ordinal, 0) AS furthest_ordinal,
               (SELECT max(a.attempt_no) FROM attempt a
                 WHERE a.module_id = m.id AND a.learner_id = %(learner)s)
                   AS attempts,
               (SELECT a.score FROM attempt a
                 WHERE a.module_id = m.id AND a.learner_id = %(learner)s
                   AND a.finished_at IS NOT NULL
                 ORDER BY a.attempt_no DESC LIMIT 1) AS latest_score
        FROM module m
        LEFT JOIN enrolment e
               ON e.module_id = m.id AND e.learner_id = %(learner)s
        WHERE m.published
        ORDER BY m.sort_order, m.id
        """,
        {"learner": learner["id"]})


@app.get("/api/modules/{slug}")
def module_detail(slug: str,
                  learner: Dict[str, Any] = Depends(auth.current_learner)):
    """The lessons, with narration. Deliberately no questions: they arrive
    when an attempt is started, so they are not sitting in the page source
    while somebody reads the slides."""
    module = _module(slug)
    db.execute(
        """
        INSERT INTO enrolment (learner_id, module_id, started_at)
        VALUES (%s, %s, now())
        ON CONFLICT (learner_id, module_id) DO UPDATE
            SET started_at = COALESCE(enrolment.started_at, now())
        """,
        (learner["id"], module["id"]))
    lessons = db.query(
        "SELECT ordinal, title, body, animation, image, narration, audio_url, "
        "narration_seconds FROM lesson WHERE module_id = %s ORDER BY ordinal",
        (module["id"],))
    enrolment = db.one(
        "SELECT started_at, completed_at, furthest_ordinal FROM enrolment "
        "WHERE learner_id = %s AND module_id = %s",
        (learner["id"], module["id"]))
    return {
        "slug": module["slug"],
        "title": module["title"],
        "summary": module["summary"],
        "minutes": module["minutes"],
        "content_hash": module["content_hash"],
        "lessons": lessons,
        "question_count": len(_questions(module["id"])),
        "enrolment": enrolment,
    }


@app.post("/api/modules/{slug}/progress")
def record_progress(slug: str, progress: Progress,
                    learner: Dict[str, Any] = Depends(auth.current_learner)):
    """How far they have got. Only ever moves forward — scrolling back to
    slide two does not mean they have unlearned slides three to eight."""
    module = _module(slug)
    row = db.one(
        """
        INSERT INTO enrolment (learner_id, module_id, started_at,
                               furthest_ordinal)
        VALUES (%s, %s, now(), %s)
        ON CONFLICT (learner_id, module_id) DO UPDATE
            SET furthest_ordinal = greatest(enrolment.furthest_ordinal,
                                            EXCLUDED.furthest_ordinal),
                started_at = COALESCE(enrolment.started_at, now())
        RETURNING furthest_ordinal
        """,
        (learner["id"], module["id"], progress.furthest_ordinal))
    return row


@app.post("/api/modules/{slug}/attempts")
def start_attempt(slug: str,
                  learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Begin the knowledge check, or hand back the one already in progress.

    Resuming rather than starting afresh matters: if a closed tab silently
    began attempt 2, the attempt number would stop meaning "how many times did
    this person need" and start meaning "how flaky is their wifi".
    """
    module = _module(slug)
    questions = _questions(module["id"])
    if not questions:
        raise HTTPException(status_code=409,
                            detail="this module has no knowledge check")

    attempt = db.one(
        "SELECT * FROM attempt WHERE learner_id = %s AND module_id = %s "
        "AND finished_at IS NULL ORDER BY attempt_no DESC LIMIT 1",
        (learner["id"], module["id"]))
    if not attempt:
        attempt = db.one(
            """
            INSERT INTO attempt (learner_id, module_id, attempt_no,
                                 content_hash, out_of)
            VALUES (%(learner)s, %(module)s,
                    COALESCE((SELECT max(attempt_no) + 1 FROM attempt
                               WHERE learner_id = %(learner)s
                                 AND module_id = %(module)s), 1),
                    %(hash)s, %(out_of)s)
            RETURNING *
            """,
            {"learner": learner["id"], "module": module["id"],
             "hash": module["content_hash"], "out_of": len(questions)})

    answered = {r["question_id"] for r in db.query(
        "SELECT question_id FROM response WHERE attempt_id = %s",
        (attempt["id"],))}
    return {
        "attempt_id": attempt["id"],
        "attempt_no": attempt["attempt_no"],
        "out_of": attempt["out_of"],
        "answered": len(answered),
        "questions": [content.question_for_learner(q) for q in questions
                      if q["id"] not in answered],
    }


@app.post("/api/attempts/{attempt_id}/responses")
def answer(attempt_id: int, answer: Answer,
           learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Grade one answer, server-side, and explain it.

    The explanation comes back whether they were right or wrong. Being told
    only "incorrect" teaches nothing at the one moment a learner is actually
    paying attention to the subject.
    """
    attempt = _owned_attempt(attempt_id, learner)
    if attempt["finished_at"]:
        raise HTTPException(status_code=409, detail="this attempt is finished")

    question = db.one(
        "SELECT id, ordinal, prompt, options, correct_index, explains, teaches "
        "FROM question WHERE module_id = %s AND ordinal = %s AND NOT retired",
        (attempt["module_id"], answer.ordinal))
    if not question:
        raise HTTPException(status_code=404, detail="no such question")

    options = question["options"]
    if answer.chosen_index is not None and not (
            0 <= answer.chosen_index < len(options)):
        raise HTTPException(status_code=422,
                            detail="that option does not exist")

    if db.one("SELECT 1 FROM response WHERE attempt_id = %s AND question_id = %s",
              (attempt_id, question["id"])):
        raise HTTPException(
            status_code=409,
            detail="already answered in this attempt; a retake is a new attempt")

    took_ms = answer.took_ms
    if took_ms is not None:
        took_ms = max(0, min(took_ms, MAX_SANE_TOOK_MS))

    outcome = content.reveal(question, answer.chosen_index)
    db.execute(
        """
        INSERT INTO response (attempt_id, question_id, chosen_index, correct,
                              took_ms, asked)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (attempt_id, question["id"], answer.chosen_index, outcome["correct"],
         took_ms,
         # The wording as it was on the screen. Content gets re-authored, and
         # a stored answer against a question nobody can reconstruct is not
         # evidence of anything.
         json.dumps({"prompt": question["prompt"], "options": options,
                     "correct_index": question["correct_index"]})))
    return outcome


@app.post("/api/attempts/{attempt_id}/finish")
def finish(attempt_id: int,
           learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Close the attempt and score it.

    An unanswered question counts as wrong rather than being left out of the
    denominator — otherwise abandoning the questions you do not know scores
    better than attempting them.
    """
    attempt = _owned_attempt(attempt_id, learner)
    if attempt["finished_at"]:
        raise HTTPException(status_code=409, detail="this attempt is finished")

    scored = db.one(
        "SELECT count(*) FILTER (WHERE correct) AS score, count(*) AS answered "
        "FROM response WHERE attempt_id = %s", (attempt_id,))
    out_of = attempt["out_of"] or scored["answered"]
    row = db.one(
        "UPDATE attempt SET finished_at = now(), score = %s, out_of = %s "
        "WHERE id = %s RETURNING attempt_no, score, out_of, finished_at",
        (scored["score"], out_of, attempt_id))
    db.execute(
        "UPDATE enrolment SET completed_at = COALESCE(completed_at, now()) "
        "WHERE learner_id = %s AND module_id = %s",
        (learner["id"], attempt["module_id"]))
    return {
        "attempt_no": row["attempt_no"],
        "score": row["score"],
        "out_of": row["out_of"],
        "unanswered": max(0, out_of - scored["answered"]),
        # Said plainly rather than left to be inferred from attempt_no: a pass
        # on the third go is not the same evidence as a pass on the first.
        "first_attempt": row["attempt_no"] == 1,
    }
