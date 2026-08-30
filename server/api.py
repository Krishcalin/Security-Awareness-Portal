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

import csv
import hashlib
import io as _io
import json
import logging
import math
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote
from typing import Any, Dict, List, Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, HTTPException,
                     Request)
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from jinja2 import Environment, FileSystemLoader, select_autoescape

from server import (auth, certificate, content, db, entra, ingest, mailer,
                    passwords, reporting)
from server.config import settings

log = logging.getLogger(__name__)

#: Anything longer than this on one question is a walk away from the desk, not
#: thinking time, and would distort the "answered in under two seconds" signal
#: it sits next to.
MAX_SANE_TOOK_MS = 30 * 60 * 1000


def _may_review(learner) -> bool:
    """Whether this person may look at a course they have already passed.

    For the people who write and check the material. It gives back the slides
    and nothing else — see `content_reviewers` in server/config.py for why the
    knowledge check is not part of it.
    """
    return bool(settings.content_reviewers) and \
        (learner.get("email") or "").casefold() in settings.content_reviewers


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    if settings.content_reviewers:
        # At WARNING, and naming them. An exception nobody can see in the log
        # is one that survives into production because nobody remembered it
        # was there.
        log.warning("CONTENT_REVIEWERS is set: %s may re-open a course they "
                    "have already passed. The knowledge check stays closed to "
                    "them.", ", ".join(sorted(settings.content_reviewers)))
    db.init_schema()
    for summary in ingest.sync():
        log.info("content loaded: %(slug)s (%(lessons)d lessons, "
                 "%(questions)d questions, %(retired)d retired)", summary)
    yield
    db.close_pool()


app = FastAPI(title="Security Awareness Portal", lifespan=lifespan)

#: Autoescaping, which is the whole reason this is a template engine and
#: not a format string. The sign-in page renders an error that can carry a
#: code returned by Microsoft, and a page that interpolates a remote value
#: into markup by hand is one crafted callback away from running script.
TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=select_autoescape(["html"]))

# The slide artwork, served from where it is authored. Copying 12MB of PNGs
# into the frontend's public/ would give two copies and one of them would go
# stale the next time a slide is redrawn.
#
# Mounted at /media rather than /assets because Vite emits the built bundle to
# /assets/, and this mount is matched first: sharing the prefix would have made
# every production build 404 on its own JavaScript while working perfectly in
# development, where Vite serves the bundle itself.
MEDIA = Path(__file__).resolve().parents[1] / "assets"
if MEDIA.is_dir():
    app.mount("/media", StaticFiles(directory=MEDIA), name="media")



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


#: Cryptographically seeded, so two servers starting in the same second do
#: not deal the same hands.
_DRAW = secrets.SystemRandom()


def _fingerprint(question_ids: list) -> str:
    """Identifies an ORDERED draw.

    Two learners given the same ten in a different order have different
    fingerprints. That is the promise that was asked for, and the looser of
    the two readings — which is the right one, because insisting no two people
    ever share a SET would start refusing draws long before it needed to.
    """
    return hashlib.sha256(
        ",".join(str(i) for i in question_ids).encode("ascii")).hexdigest()


def _deal(bank: List[Dict[str, Any]], how_many: int) -> List[int]:
    """`sample` rather than `shuffle` and slice: it returns them already in a
    random ORDER, which is half of what has to be unique."""
    return [q["id"] for q in _DRAW.sample(bank, min(how_many, len(bank)))]


def _attempt_questions(attempt_id: int) -> List[Dict[str, Any]]:
    """This attempt's questions, in the order they were dealt."""
    return db.query(
        """
        SELECT q.id, q.ordinal, q.prompt, q.options, q.correct_index,
               q.explains, q.teaches, aq.position
        FROM attempt_question aq JOIN question q ON q.id = aq.question_id
        WHERE aq.attempt_id = %s ORDER BY aq.position
        """, (attempt_id,))


def _begin_attempt(learner: Dict[str, Any], module: Dict[str, Any],
                   bank: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deal a hand, and write it down.

    The unique index on (module_id, question_set) is what makes "no two
    attempts get the same ten in the same order" a fact rather than a
    probability: a repeat cannot be stored, so the draw is simply taken again.
    With a hundred questions there are about 6e19 ordered tens, so this is not
    expected to fire — it is here so the guarantee does not rest on that
    expectation being right.
    """
    import psycopg

    for _ in range(8):
        drawn = _deal(bank, settings.quiz_length)
        try:
            with db.connection() as conn:
                attempt = conn.execute(
                    """
                    INSERT INTO attempt (learner_id, module_id, attempt_no,
                                         content_hash, out_of, question_set)
                    VALUES (%(learner)s, %(module)s,
                            COALESCE((SELECT max(attempt_no) + 1 FROM attempt
                                       WHERE learner_id = %(learner)s
                                         AND module_id = %(module)s), 1),
                            %(hash)s, %(out_of)s, %(fingerprint)s)
                    RETURNING *
                    """,
                    {"learner": learner["id"], "module": module["id"],
                     "hash": module["content_hash"], "out_of": len(drawn),
                     "fingerprint": _fingerprint(drawn)}).fetchone()
                for position, question_id in enumerate(drawn):
                    conn.execute(
                        "INSERT INTO attempt_question (attempt_id, position, "
                        "question_id) VALUES (%s, %s, %s)",
                        (attempt["id"], position, question_id))
                conn.commit()
                return attempt
        except psycopg.errors.UniqueViolation:
            log.info("redrawing: that exact hand has been dealt before")
    raise HTTPException(status_code=503,
                        detail="could not draw a question set; please retry")


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
            "department": learner["department"],
            # So the app knows whether to offer the reporting screen at all.
            # It is not what authorises it — every report endpoint checks for
            # itself — it only decides whether a link is drawn.
            "role": learner.get("role", "learner")}


def _already_passed(learner_id: int, module_id: int):
    """The certificate this person already holds for this module, or None.

    THE CERTIFICATE ROW IS THE RECORD OF A SUCCESSFUL COMPLETION. It names the
    attempt that earned it, the score as it stood, the date, and the name as
    printed. There is deliberately no second `passed` flag on the enrolment:
    a second place to write it down is a second place for it to disagree, and
    the pass mark is a setting that can change — a flag derived fresh each time
    would quietly un-pass people the day somebody moved the threshold, while a
    certificate is a statement about a moment that stays true.

    The earliest one, when there are several. A retake that passes again does
    not move the date somebody completed the training.
    """
    return db.one(
        """
        SELECT c.serial, c.issued_at, c.score, c.out_of, c.name_printed,
               a.attempt_no
        FROM certificate c JOIN attempt a ON a.id = c.attempt_id
        WHERE c.learner_id = %s AND c.module_id = %s
        ORDER BY c.issued_at LIMIT 1
        """, (learner_id, module_id))


@app.get("/api/modules")
def modules(learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Every published module, with this learner's standing in each.

    `furthest_ordinal` is reported beside `completed_at` rather than folded
    into it, so a module people open and abandon on slide three is visible as
    that, instead of rounding to "not completed" alongside the people who
    never opened it.
    """
    listed = db.query(
        """
        SELECT m.slug, m.title, m.summary, m.minutes, m.topic,
               m.content_hash,
               (SELECT count(*) FROM lesson l WHERE l.module_id = m.id)
                   AS lessons,
               -- What a learner ANSWERS, not how many are in the bank. The
               -- card says "10 questions" and a score reads "8 of 10";
               -- reporting the bank size would make both of those wrong.
               LEAST(%(quiz_length)s,
                     (SELECT count(*) FROM question q
                       WHERE q.module_id = m.id AND NOT q.retired)) AS questions,
               (SELECT count(*) FROM question q
                 WHERE q.module_id = m.id AND NOT q.retired) AS bank,
               e.started_at, e.completed_at,
               COALESCE(e.furthest_ordinal, 0) AS furthest_ordinal,
               COALESCE(e.last_ordinal, 0) AS last_ordinal,
               (SELECT max(a.attempt_no) FROM attempt a
                 WHERE a.module_id = m.id AND a.learner_id = %(learner)s)
                   AS attempts,
               (SELECT a.score FROM attempt a
                 WHERE a.module_id = m.id AND a.learner_id = %(learner)s
                   AND a.finished_at IS NOT NULL
                 ORDER BY a.attempt_no DESC LIMIT 1) AS latest_score,
               -- Whether they passed is decided here, against the same
               -- threshold `finish` uses. The browser is not asked to work it
               -- out from a score, because then there are two answers.
               EXISTS (SELECT 1 FROM certificate c
                        WHERE c.module_id = m.id
                          AND c.learner_id = %(learner)s) AS passed,
               (SELECT c.serial FROM certificate c
                 WHERE c.module_id = m.id AND c.learner_id = %(learner)s
                 ORDER BY c.issued_at DESC LIMIT 1) AS certificate_serial
        FROM module m
        LEFT JOIN enrolment e
               ON e.module_id = m.id AND e.learner_id = %(learner)s
        WHERE m.published
        ORDER BY m.sort_order, m.id
        """,
        {"learner": learner["id"], "quiz_length": settings.quiz_length})
    reviewer = _may_review(learner)
    for row in listed:
        # Without this the front page offers no way into a course somebody has
        # passed, and a reviewer allowed to read it again has nowhere to click.
        row["reviewing"] = reviewer and row["passed"]
    return listed


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
        "audio_timings_url, narration_seconds "
        "FROM lesson WHERE module_id = %s ORDER BY ordinal",
        (module["id"],))
    passed = _already_passed(learner["id"], module["id"])
    reviewing = bool(passed) and _may_review(learner)

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
        # Withheld once the training has been passed, rather than left to the
        # browser to hide. A rule the client enforces is a rule that holds
        # until somebody opens the network tab.
        #
        # WHAT THIS IS AND IS NOT. It closes the course and protects the
        # record; it is not an attempt to make the material unreachable. The
        # artwork and the recordings are served from /media, which is static
        # and unauthenticated, so anybody with a URL still has them.
        "lessons": [] if passed and not reviewing else lessons,
        "completed": passed,
        # Set when somebody is being shown a course they have already passed
        # because their address is listed. The screen says so: an exception
        # that looks from the inside exactly like not having passed is one
        # that gets mistaken for a bug in the lock.
        "reviewing": reviewing,
        # How many they will be asked, and how large a bank that is drawn
        # from — the second is worth saying, because it is the reason a retake
        # is not the same quiz over again.
        "question_count": min(settings.quiz_length,
                              len(_questions(module["id"]))),
        "question_bank": len(_questions(module["id"])),
        "enrolment": enrolment,
    }


@app.post("/api/modules/{slug}/progress")
def record_progress(slug: str, progress: Progress,
                    learner: Dict[str, Any] = Depends(auth.current_learner)):
    """How far they have got. Only ever moves forward — scrolling back to
    slide two does not mean they have unlearned slides three to eight."""
    module = _module(slug)
    if _already_passed(learner["id"], module["id"]):
        # Nothing left to record. Somebody with a tab still open from before
        # they passed must not be able to move the record behind the
        # certificate that has already been issued.
        if _may_review(learner):
            # A reviewer re-reading the course is not making progress through
            # it. Accepted and dropped rather than refused, so that looking at
            # the material does not fill the console with errors — and their
            # own record does not move either way.
            return db.one(
                "SELECT furthest_ordinal, last_ordinal FROM enrolment "
                "WHERE learner_id = %s AND module_id = %s",
                (learner["id"], module["id"]))
        raise HTTPException(status_code=409,
                            detail="this training has already been passed")
    row = db.one(
        """
        INSERT INTO enrolment (learner_id, module_id, started_at,
                               furthest_ordinal, last_ordinal, last_seen_at)
        VALUES (%s, %s, now(), %s, %s, now())
        ON CONFLICT (learner_id, module_id) DO UPDATE
            SET furthest_ordinal = greatest(enrolment.furthest_ordinal,
                                            EXCLUDED.furthest_ordinal),
                -- Where they actually are, which is not the same number.
                -- Somebody at slide 18 who goes back to 4 to re-read it and
                -- then closes the tab left at 4, and that is where they
                -- should resume; their progress is still 18.
                last_ordinal = EXCLUDED.last_ordinal,
                last_seen_at = now(),
                started_at = COALESCE(enrolment.started_at, now())
        RETURNING furthest_ordinal, last_ordinal
        """,
        (learner["id"], module["id"], progress.furthest_ordinal,
         progress.furthest_ordinal))
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
    passed = _already_passed(learner["id"], module["id"])
    if passed:
        # The attempt that earned the certificate is the record. Another one
        # could only either repeat it or contradict it, and the second is
        # worse: a report showing somebody passed and then failed says
        # something about them that the certificate in their inbox denies.
        raise HTTPException(status_code=409,
                            detail="this training has already been passed")

    questions = _questions(module["id"])
    if not questions:
        raise HTTPException(status_code=409,
                            detail="this module has no knowledge check")

    attempt = db.one(
        "SELECT * FROM attempt WHERE learner_id = %s AND module_id = %s "
        "AND finished_at IS NULL ORDER BY attempt_no DESC LIMIT 1",
        (learner["id"], module["id"]))
    if not attempt:
        attempt = _begin_attempt(learner, module, questions)

    answered = {r["question_id"] for r in db.query(
        "SELECT question_id FROM response WHERE attempt_id = %s",
        (attempt["id"],))}
    dealt = _attempt_questions(attempt["id"])
    return {
        "attempt_id": attempt["id"],
        "attempt_no": attempt["attempt_no"],
        "out_of": attempt["out_of"],
        "answered": len(answered),
        # In the order they were dealt, and only the ones still outstanding —
        # so closing the tab and coming back resumes rather than restarts.
        "questions": [content.question_for_learner(q) for q in dealt
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

    # Constrained to this attempt's own draw. Looking the question up by
    # ordinal alone would let somebody answer any of the hundred, including
    # the ninety they were never asked.
    question = db.one(
        """
        SELECT q.id, q.ordinal, q.prompt, q.options, q.correct_index,
               q.explains, q.teaches
        FROM attempt_question aq JOIN question q ON q.id = aq.question_id
        WHERE aq.attempt_id = %s AND q.ordinal = %s AND NOT q.retired
        """, (attempt_id, answer.ordinal))
    if not question:
        raise HTTPException(status_code=404,
                            detail="that question is not in this attempt")

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
def finish(attempt_id: int, background: BackgroundTasks,
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

    # The pass mark lives here and nowhere else. A threshold the browser also
    # holds is a threshold the two can disagree about, and the disagreement
    # shows up as a certificate the server never awarded.
    passed = out_of > 0 and row["score"] / out_of >= settings.pass_mark
    issued = None
    if passed:
        module = db.one("SELECT * FROM module WHERE id = %s",
                        (attempt["module_id"],))
        issued = _issue(learner, module, attempt_id, row["score"], out_of)
        # After the response. Nobody waits on a mail server to be shown the
        # result they just earned, and the certificate is downloadable whether
        # or not the email ever leaves.
        background.add_task(_deliver, issued["id"])

    return {
        "attempt_no": row["attempt_no"],
        "score": row["score"],
        "out_of": row["out_of"],
        "unanswered": max(0, out_of - scored["answered"]),
        # Said plainly rather than left to be inferred from attempt_no: a pass
        # on the third go is not the same evidence as a pass on the first.
        "first_attempt": row["attempt_no"] == 1,
        "passed": passed,
        "pass_mark": settings.pass_mark,
        "needed": math.ceil(out_of * settings.pass_mark) if out_of else 0,
        "certificate": {
            "serial": issued["serial"],
            "name_printed": issued["name_printed"],
            "issued_at": issued["issued_at"],
            # What the portal will attempt, said before it is attempted, so
            # "check your inbox" is not printed at somebody whose certificate
            # was never going to be posted anywhere.
            "will_email_to": learner["email"] if settings.mail_configured else "",
        } if issued else None,
    }


def resume_path(learner_id: int) -> str:
    """Where this person left off, as a path the app can be sent to.

    An unfinished attempt wins over a slide: somebody who stopped in the middle
    of the questions is further on than the last slide they looked at, and
    dropping them back into the deck would lose the answers they had already
    given.
    """
    attempt = db.one(
        """
        SELECT m.slug FROM attempt a JOIN module m ON m.id = a.module_id
        WHERE a.learner_id = %s AND a.finished_at IS NULL
        ORDER BY a.started_at DESC LIMIT 1
        """, (learner_id,))
    if attempt:
        return "/module/%s/check" % attempt["slug"]

    open_module = db.one(
        """
        SELECT m.slug, e.last_ordinal, e.furthest_ordinal
        FROM enrolment e JOIN module m ON m.id = e.module_id
        WHERE e.learner_id = %s AND e.completed_at IS NULL AND m.published
          -- And not one they have already passed. Sending somebody back to
          -- slide twelve of a course they finished last month is the portal
          -- telling them it has not noticed.
          AND NOT EXISTS (SELECT 1 FROM certificate c
                           WHERE c.learner_id = e.learner_id
                             AND c.module_id = e.module_id)
        ORDER BY e.last_seen_at DESC NULLS LAST, e.started_at DESC LIMIT 1
        """, (learner_id,))
    if not open_module:
        return "/"
    at = open_module["last_ordinal"] or open_module["furthest_ordinal"] or 0
    return ("/module/%s?slide=%d" % (open_module["slug"], at) if at > 1
            else "/module/%s" % open_module["slug"])


@app.get("/api/resume")
def resume(learner: Dict[str, Any] = Depends(auth.current_learner)):
    """Used by the app on load, so a fresh tab lands where the last one
    stopped rather than at the beginning."""
    return {"path": resume_path(learner["id"])}


# ── certificates ───────────────────────────────────────────────────────────

def _serial() -> str:
    """Readable, and not guessable. Downloads are checked against ownership
    rather than against the serial being secret, but a certificate people
    quote to each other should not also be a sequence anybody can walk."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1
    body = "".join(secrets.choice(alphabet) for _ in range(8))
    return "SAT-%d-%s" % (datetime.now(timezone.utc).year, body)


def _issue(learner: Dict[str, Any], module: Dict[str, Any], attempt_id: int,
           score: int, out_of: int) -> Dict[str, Any]:
    """Record the certificate. The name is stored AS PRINTED.

    A certificate is a statement about a moment. Somebody changing their
    surname next year must not retrospectively alter the document they were
    sent, and re-rendering must not quietly produce something different from
    what went out by email.
    """
    name = certificate.printed_name(
        learner.get("given_name", ""), learner.get("family_name", ""),
        learner.get("display_name", ""), learner.get("email", ""))
    return db.one(
        """
        INSERT INTO certificate (serial, learner_id, module_id, attempt_id,
                                 name_printed, score, out_of)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (attempt_id) DO UPDATE SET serial = certificate.serial
        RETURNING *
        """,
        (_serial(), learner["id"], module["id"], attempt_id, name,
         score, out_of))


def _deliver(certificate_id: int) -> None:
    """Email the certificate, and write down what happened either way.

    Runs after the response, so nobody waits on a mail server to see the
    result they have just earned.
    """
    row = db.one(
        "SELECT c.*, l.email FROM certificate c JOIN learner l "
        "ON l.id = c.learner_id WHERE c.id = %s", (certificate_id,))
    if not row or row["emailed_at"]:
        return
    try:
        pdf = certificate.render(row["name_printed"],
                                 row["issued_at"].date(), row["serial"])
        mailer.send_certificate(row["email"], row["name_printed"], pdf,
                                row["serial"], row["score"], row["out_of"])
    except Exception as problem:                        # noqa: BLE001
        # Including mailer.NotConfigured. A failure to send is a fact about
        # this certificate, not an error to raise at somebody who has just
        # finished their training — and it is recorded so it can be retried
        # and so nobody is told it was sent when it was not.
        log.warning("certificate %s not emailed: %s", row["serial"], problem)
        db.execute("UPDATE certificate SET email_error = %s WHERE id = %s",
                   (str(problem)[:500], certificate_id))
        return
    db.execute("UPDATE certificate SET emailed_at = now(), emailed_to = %s, "
               "email_error = '' WHERE id = %s", (row["email"], certificate_id))


@app.get("/api/certificates/{serial}")
def download_certificate(serial: str,
                         learner: Dict[str, Any] = Depends(auth.current_learner)):
    """The PDF. Yours only — the serial is not a password."""
    row = db.one("SELECT * FROM certificate WHERE serial = %s", (serial,))
    if not row or row["learner_id"] != learner["id"]:
        raise HTTPException(status_code=404, detail="no such certificate")
    pdf = certificate.render(row["name_printed"], row["issued_at"].date(),
                             row["serial"])
    return StreamingResponse(
        _io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition":
                 'attachment; filename="Security-Awareness-Certificate-%s.pdf"'
                 % row["serial"]})

# ── reporting ──────────────────────────────────────────────────────────────
#
# Everything behind `require_admin`, which 404s rather than 403s for anybody
# else. These are INDIVIDUAL results, not anonymous statistics. The sign-in
# page tells people their completion status is recorded; it does not say that
# a named result is visible to the security team, so do not treat this screen
# as something people have already been told about.

@app.get("/api/report/{slug}")
def report(slug: str,
           admin: Dict[str, Any] = Depends(auth.require_admin)):
    """Everything except the per-person list, which is fetched separately
    because it is the only part that grows with the organisation."""
    module = _module(slug)
    return {
        "module": {"slug": module["slug"], "title": module["title"],
                   "content_hash": module["content_hash"]},
        # Several numbers rather than one. The single figure everybody wants
        # is "94% trained", and it is the reason this whole product exists.
        "summary": reporting.summary(module["id"]),
        "questions": reporting.questions(module["id"]),
        "slides": reporting.slides(module["id"]),
        "departments": reporting.departments(module["id"]),
        "delivery": reporting.delivery(module["id"]),
        "thresholds": {
            "min_answers": reporting.MIN_ANSWERS,
            "not_discriminating": reporting.NOT_DISCRIMINATING,
            "material_failed": reporting.MATERIAL_FAILED,
            "pass_mark": settings.pass_mark,
        },
    }


@app.get("/api/report/{slug}/people")
def report_people(slug: str,
                  admin: Dict[str, Any] = Depends(auth.require_admin)):
    return reporting.people(_module(slug)["id"])


@app.get("/api/report/{slug}/export.csv")
def report_export(slug: str,
                  admin: Dict[str, Any] = Depends(auth.require_admin)):
    """The record a regulator asks for.

    Completion and result are separate columns and stay that way. A single
    "trained" column is the thing this file is designed not to be, because it
    is what gets pasted into a board pack and read as evidence of awareness.
    """
    module = _module(slug)
    buffer = _io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "email", "name", "department",
        "started", "furthest_slide", "reached_end",
        "attempts", "latest_score", "out_of",
        "passed", "passed_on_attempt", "certificate", "certificate_issued",
    ])
    for row in reporting.people(module["id"]):
        writer.writerow([
            row["email"], row["display_name"], row["department"],
            row["started_at"].isoformat() if row["started_at"] else "",
            row["furthest_ordinal"],
            row["completed_at"].isoformat() if row["completed_at"] else "",
            row["attempts"] or 0,
            "" if row["latest_score"] is None else row["latest_score"],
            "" if row["out_of"] is None else row["out_of"],
            "yes" if row["certificate"] else "no",
            row["passed_on_attempt"] or "",
            row["certificate"] or "",
            row["issued_at"].isoformat() if row["issued_at"] else "",
        ])
    filename = "%s-training-record-%s.csv" % (
        module["slug"], datetime.now(timezone.utc).date().isoformat())
    return StreamingResponse(
        _io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename})

# ── signing in ─────────────────────────────────────────────────────────────
# Declared before the catch-all below, or the front page would be served for
# every one of these.

#: Carries who is changing their password, for somebody who has proved a
#: password but has not been given a session yet because that password was
#: issued to them by somebody else. Fifteen minutes: it exists to cross one
#: page.
PASSWORD_COOKIE = "awareness_password_change"
PASSWORD_COOKIE_MAX_AGE = 15 * 60


def _write_session(response: Response, token: str) -> None:
    """One place that decides how the session cookie is written.

    HttpOnly so script cannot read it, SameSite=Lax so it is not sent on a
    cross-site POST but survives the top-level redirect back from Microsoft,
    and Secure unless it has been explicitly switched off for local http.
    """
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=auth.MAX_AGE_SECONDS, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/")


def _set_session(response: Response, entra_oid: str, epoch: int = 0) -> None:
    """A session for a directory identity."""
    _write_session(response, auth.issue(entra_oid, epoch))


def _set_local_session(response: Response, learner: Dict[str, Any]) -> None:
    """A session for an account that signed in with a password."""
    _write_session(response,
                   auth.issue_local(learner["id"], learner["session_epoch"]))


#: The sign-in form is four short fields. Anything approaching this is not
#: one, and reading it into memory first would be doing an anonymous caller a
#: favour.
MOST_FORM_BYTES = 8 * 1024


async def _form_fields(request: Request) -> Dict[str, str]:
    """The fields of an ordinary HTML form post.

    `request.form()` would do this, and would pull in python-multipart to do
    it — a dependency for parsing `a=1&b=2`, on a server whose whole reason for
    existing is to be installable inside an organisation that will look at
    what it installs. These are the only forms this application has, they have
    no file upload, and urlencoded is what a browser sends for them.
    """
    if not request.headers.get("content-type", "").split(";")[0].strip() \
            == "application/x-www-form-urlencoded":
        return {}
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MOST_FORM_BYTES:
        return {}
    body = await request.body()
    if len(body) > MOST_FORM_BYTES:
        return {}
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    # Last one wins, which is also what a form library does. A password field
    # sent twice is a client doing something odd, not a way past anything.
    return dict(parse_qsl(decoded, keep_blank_values=True))


def _form_token(purpose: str) -> str:
    """A signed statement that this form came from this server.

    Without one, a page on another site can post the sign-in form and put
    somebody into an account that is not theirs. That is not a way in for the
    attacker, which is why it gets forgotten — it is a way to have somebody
    else's training, and the certificate at the end of it, recorded against an
    account the attacker holds.
    """
    return auth.sign({"form": purpose})


def _form_token_ok(token: str, purpose: str) -> bool:
    claims = auth.verify(token, auth.FORM_MAX_AGE_SECONDS)
    return bool(claims and claims.get("form") == purpose)


def _sign_in_page(problem: str = "", status: int = 200,
                  next_path: str = "/", email: str = "") -> HTMLResponse:
    """The sign-in screen, with an optional sentence about what went wrong.

    Rendered through the template rather than assembled here: `problem` can
    carry an error code that came back from Microsoft, which is remote input,
    and hand-interpolating that into markup is one crafted callback away from
    running script in the browser. Autoescaping makes that a non-question.
    """
    query = ""
    if next_path and next_path != "/":
        query = "?next=" + quote(next_path, safe="")
    return HTMLResponse(status_code=status, content=(
        TEMPLATES.get_template("index.html").render(
            problem=problem,
            configured=entra.configured(),
            # Put back so a mistyped password does not also mean retyping the
            # address. Autoescaped, like everything else on this page.
            email=email,
            form_token=_form_token("signin"),
            next_query=query)))


@app.get("/auth/login", include_in_schema=False)
def sign_in_page(next: str = "/") -> Response:
    """The page somebody lands on. It does not redirect to Microsoft on its
    own: an automatic bounce off-site gives no chance to see whose portal this
    is, and "I clicked a link and ended up on a Microsoft password box" is the
    shape of the thing this course spends twenty minutes warning people about.
    """
    return _sign_in_page(next_path=entra.safe_next(next))


@app.get("/auth/start", include_in_schema=False)
def sign_in_start(next: str = "/") -> Response:
    if not entra.configured():
        return _sign_in_page(
            "Microsoft sign-in is not configured on this server. Set the "
            "ENTRA_* values in the environment; see .env.example.", 503)
    url, flow = entra.begin(next)
    response = RedirectResponse(url, status_code=307)
    # state, nonce and the PKCE verifier have to survive the trip to Microsoft
    # and there is no server-side session for somebody not yet signed in. The
    # signature is what makes a callback carrying a state this server never
    # issued refusable rather than merely unfamiliar.
    response.set_cookie(
        entra.FLOW_COOKIE, auth.sign(flow),
        max_age=entra.FLOW_MAX_AGE_SECONDS, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/auth")
    return response


@app.get("/auth/callback", include_in_schema=False)
def sign_in_callback(request: Request) -> Response:
    flow = auth.verify(request.cookies.get(entra.FLOW_COOKIE, ""),
                       entra.FLOW_MAX_AGE_SECONDS)
    if not flow:
        return _sign_in_page(
            "This sign-in did not start here, or it took too long. Please "
            "start again.", 400)
    try:
        identity = entra.complete(flow, dict(request.query_params))
    except entra.SignInRefused as refused:
        return _sign_in_page(str(refused), 403)

    learner = auth.upsert_learner(
        entra_oid=identity["oid"], email=identity["email"],
        upn=identity["upn"], display_name=identity["display_name"],
        given_name=identity["given_name"], family_name=identity["family_name"],
        role=identity.get("role"))

    # Straight back to where they left off. Somebody who signed out halfway
    # through slide twelve should not have to find their way back to slide
    # twelve; a deep link they followed still wins over this.
    target = entra.safe_next(flow.get("next"))
    if target == "/":
        target = resume_path(learner["id"])
    response = RedirectResponse(target, status_code=303)
    _set_session(response, identity["oid"], learner["session_epoch"])
    response.delete_cookie(entra.FLOW_COOKIE, path="/auth")
    return response


# ── signing in with a password ─────────────────────────────────────────────
#
# The second way in, for the people the directory does not reach. Everything
# that makes it survivable is in `auth.password_sign_in` — one answer for a
# wrong password and for an unknown address, the same work done either way so
# the two cannot be told apart with a stopwatch, and a lockout after ten
# consecutive failures.

@app.post("/auth/password", include_in_schema=False)
async def password_sign_in(request: Request, next: str = "/") -> Response:
    fields = await _form_fields(request)
    email = fields.get("email", "")
    password = fields.get("password", "")

    target = entra.safe_next(next)
    if not _form_token_ok(fields.get("form_token", ""), "signin"):
        return _sign_in_page(
            "This form did not come from here, or it has been open too long. "
            "Please try again.", 400, next_path=target, email=email)

    try:
        # scrypt is deliberately slow and this handler is on the event loop.
        # Off it, or one person signing in stops every other request for a
        # sixth of a second.
        learner = await run_in_threadpool(auth.password_sign_in, email,
                                          password)
    except auth.Refused as refused:
        # Recorded, because ten of these in a row is the thing somebody needs
        # to be able to look up afterwards. Only when it looks like an address:
        # a password typed into the email box by mistake should not be the one
        # thing about this attempt that gets written down.
        log.info("password sign-in refused for %s",
                 email if "@" in email else "(not an address)")
        return _sign_in_page(str(refused), 401, next_path=target, email=email)

    if learner["password_must_change"]:
        # No session yet. They have proved a password somebody else chose,
        # which is enough to be allowed to choose their own and nothing else.
        response = RedirectResponse("/auth/password/change", status_code=303)
        response.set_cookie(
            PASSWORD_COOKIE,
            auth.sign({"chg": learner["id"], "ep": learner["session_epoch"]}),
            max_age=PASSWORD_COOKIE_MAX_AGE, httponly=True, samesite="lax",
            secure=settings.cookie_secure, path="/auth")
        return response

    if target == "/":
        target = resume_path(learner["id"])
    response = RedirectResponse(target, status_code=303)
    _set_local_session(response, learner)
    return response


# ── choosing a password ────────────────────────────────────────────────────

def _who_is_changing(request: Request):
    """(learner, how they got here), for the change-password page.

    Two ways to be here and they are not the same. Somebody signed in is
    changing a password they already chose. Somebody holding the ticket issued
    a moment ago has proved a password an administrator gave them and has no
    session at all — which is the point: a password somebody else knows is
    good for reaching this page and nothing else.
    """
    claims = auth.read(request.cookies.get(auth.COOKIE_NAME, ""))
    if claims:
        learner = auth.learner_for(claims)
        if learner:
            return learner, ("oid" if claims.get("oid") else "lid")

    ticket = auth.verify(request.cookies.get(PASSWORD_COOKIE, ""),
                         PASSWORD_COOKIE_MAX_AGE)
    if ticket and ticket.get("chg"):
        learner = auth.learner_for({"lid": ticket["chg"],
                                    "ep": ticket.get("ep", 0)})
        if learner:
            return learner, "ticket"
    return None, ""


def _password_page(learner: Dict[str, Any], problem: str = "",
                   status: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=(
        TEMPLATES.get_template("password.html").render(
            problem=problem,
            email=learner["email"],
            forced=bool(learner["password_must_change"]),
            min_length=passwords.MIN_LENGTH,
            form_token=_form_token("password"))))


@app.get("/auth/password/change", include_in_schema=False)
def password_change_page(request: Request) -> Response:  # noqa: D401
    learner, how = _who_is_changing(request)
    if not learner:
        return _sign_in_page("Please sign in first.", 401)
    if not auth.has_password(learner["id"]):
        return _sign_in_page(
            "That account signs in with Microsoft and has no password here, "
            "so there is none to change.", 400)
    return _password_page(learner)


@app.post("/auth/password/change", include_in_schema=False)
async def password_change(request: Request) -> Response:
    fields = await _form_fields(request)
    current = fields.get("current", "")
    fresh = fields.get("fresh", "")
    again = fields.get("again", "")

    learner, how = _who_is_changing(request)
    if not learner:
        return _sign_in_page("Please sign in first.", 401)
    if not _form_token_ok(fields.get("form_token", ""), "password"):
        return _password_page(
            learner, "This form did not come from here, or it has been open "
            "too long. Please try again.", 400)
    if not auth.has_password(learner["id"]):
        return _sign_in_page(
            "That account signs in with Microsoft and has no password here, "
            "so there is none to change.", 400)

    # The current password is checked through the same path as a sign-in, so
    # this form is behind the same lockout. A change form that will take
    # unlimited guesses is a sign-in form that will, one page further in.
    try:
        await run_in_threadpool(auth.password_sign_in, learner["email"],
                                current)
    except auth.Refused as refused:
        return _password_page(learner, str(refused), 401)

    if fresh != again:
        return _password_page(
            learner, "The two new passwords are not the same.", 400)
    if fresh == current:
        return _password_page(
            learner, "That is the password you already have. Please choose a "
            "different one.", 400)
    try:
        passwords.check_suitable(fresh, learner["email"])
    except passwords.Unsuitable as unsuitable:
        return _password_page(learner, str(unsuitable), 400)

    await run_in_threadpool(auth.set_password, learner["id"], fresh, False)

    # `set_password` ends every session held under the old password, this one
    # included. Issue a new one of the same kind, or changing a password would
    # sign somebody out for having done the right thing.
    fresh_row = db.one("SELECT %s FROM learner WHERE id = %%s"
                       % auth.LEARNER_COLUMNS, (learner["id"],))
    response = RedirectResponse(resume_path(learner["id"]), status_code=303)
    if how == "oid":
        _set_session(response, learner["entra_oid"],
                     fresh_row["session_epoch"])
    else:
        _set_local_session(response, fresh_row)
    response.delete_cookie(PASSWORD_COOKIE, path="/auth")
    return response

@app.get("/auth/dev", include_in_schema=False)
def dev_sign_in(token: str = "", next: str = "/") -> Response:
    """Redeem a session token minted by `python -m server.devsession --link`.

    THIS IS NOT A WAY IN. It accepts nothing but a token already signed with
    SESSION_SECRET, and anybody who can produce one of those can set the
    cookie directly — this only saves them the trouble. It is not the thing
    this file refuses to have, which is an endpoint that takes an email
    address and believes it.

    It is still gated twice, because a convenience that survives into
    production is how a URL in someone's browser history becomes a session:
    off unless ALLOW_DEV_SIGNIN is set, and refused outright the moment Entra
    is configured, so it can never sit alongside real sign-in.
    """
    if not settings.allow_dev_signin or entra.configured():
        # 404 rather than 403: a route that says "not enabled" tells a scanner
        # that it exists and is worth coming back for.
        raise HTTPException(status_code=404, detail="not found")
    claims = auth.read(token)
    if not claims:
        return _sign_in_page("That development sign-in link is not valid or "
                             "has expired. Mint another with "
                             "`python -m server.devsession --link`.", 400)
    log.warning("development sign-in redeemed for %s", claims["oid"])
    learner = auth.learner_for(claims)
    if not learner:
        return _sign_in_page("That development sign-in link names an account "
                             "that is no longer here.", 400)
    response = RedirectResponse(entra.safe_next(next) if next != "/"
                                else resume_path(learner["id"]),
                                status_code=303)
    _set_session(response, claims["oid"], learner["session_epoch"])
    return response


@app.get("/auth/logout", include_in_schema=False)
def sign_out(request: Request) -> Response:
    """Sign out here, and at Microsoft if that is where they signed in.

    Clearing only this cookie leaves the Microsoft session intact, so the next
    click on "sign in" silently signs the same person straight back in. On a
    shared machine that is not a sign-out at all, whatever the button said.

    Only for a session that came from Microsoft, though. Sending somebody who
    signed in with a password to a Microsoft sign-out page ends nothing, and
    it is an unexpected bounce to a login-looking screen on another domain —
    which is the exact shape this course teaches people to distrust.
    """
    claims = auth.read(request.cookies.get(auth.COOKIE_NAME, "")) or {}
    target = "/"
    if claims.get("oid") and entra.configured():
        root = settings.entra_redirect_uri.split("/auth/")[0] or "/"
        target = ("%s/oauth2/v2.0/logout?post_logout_redirect_uri=%s"
                  % (entra.authority(), quote(root, safe="")))
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    # In case they got as far as the change-password page and stopped.
    response.delete_cookie(PASSWORD_COOKIE, path="/auth")
    return response

# ── the app itself ─────────────────────────────────────────
#
# Registered after every /api route on purpose. Starlette matches routes in the
# order they are added, so a catch-all declared any earlier in this file
# swallows the whole API — which is what happened the first time, and it looks
# like every endpoint returning the front page.
SPA = Path(__file__).resolve().parent / "spa"
if SPA.is_dir():
    app.mount("/assets", StaticFiles(directory=SPA / "assets"),
              name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Serve the app, and let it own its own URLs.

        A plain static mount 404s on /module/essentials, because that is a
        route inside the app rather than a file on disk. Everything works until
        somebody refreshes the page or follows a link into the middle of a
        course, which is the moment a learner is least inclined to work out
        what went wrong.
        """
        candidate = (SPA / path).resolve()
        # `path` comes from the URL, so ../.. has to be ruled out rather than
        # assumed away.
        if path and candidate.is_file() and SPA in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(SPA / "index.html")
