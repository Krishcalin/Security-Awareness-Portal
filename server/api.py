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

import io as _io
import json
import math
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any, Dict, List, Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, HTTPException,
                     Request)
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import auth, certificate, content, db, entra, ingest, mailer
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

# ── signing in ─────────────────────────────────────────────────────────────
# Declared before the catch-all below, or the front page would be served for
# every one of these.

def _set_session(response: Response, entra_oid: str) -> None:
    """One place that decides how the session cookie is written.

    HttpOnly so script cannot read it, SameSite=Lax so it is not sent on a
    cross-site POST but survives the top-level redirect back from Microsoft,
    and Secure unless it has been explicitly switched off for local http.
    """
    response.set_cookie(
        auth.COOKIE_NAME, auth.issue(entra_oid),
        max_age=auth.MAX_AGE_SECONDS, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/")


def _sign_in_problem(message: str, status: int = 400) -> HTMLResponse:
    """A sentence a person can act on, rather than a JSON error body rendered
    as text in the middle of a browser navigation."""
    return HTMLResponse(status_code=status, content=(
        "<!doctype html><html lang=\"en-GB\"><head><meta charset=\"utf-8\">"
        "<title>Sign-in</title><style>body{font:16px/1.6 system-ui,sans-serif;"
        "margin:16vh auto;max-width:34rem;padding:0 1.5rem}"
        "a{color:inherit}</style></head><body>"
        "<h1>Could not sign you in</h1><p>%s</p>"
        "<p><a href=\"/auth/login\">Try again</a></p></body></html>" % message))


@app.get("/auth/login", include_in_schema=False)
def sign_in(next: str = "/") -> Response:
    if not entra.configured():
        return _sign_in_problem(
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
        return _sign_in_problem(
            "This sign-in did not start here, or it took too long. Please "
            "start again.")
    try:
        identity = entra.complete(flow, dict(request.query_params))
    except entra.SignInRefused as refused:
        return _sign_in_problem(str(refused), 403)

    learner = auth.upsert_learner(
        entra_oid=identity["oid"], email=identity["email"],
        upn=identity["upn"], display_name=identity["display_name"],
        given_name=identity["given_name"], family_name=identity["family_name"])

    # Straight back to where they left off. Somebody who signed out halfway
    # through slide twelve should not have to find their way back to slide
    # twelve; a deep link they followed still wins over this.
    target = entra.safe_next(flow.get("next"))
    if target == "/":
        target = resume_path(learner["id"])
    response = RedirectResponse(target, status_code=303)
    _set_session(response, identity["oid"])
    response.delete_cookie(entra.FLOW_COOKIE, path="/auth")
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
        return _sign_in_problem("That development sign-in link is not valid "
                                "or has expired. Mint another with "
                                "`python -m server.devsession --link`.")
    log.warning("development sign-in redeemed for %s", claims["oid"])
    response = RedirectResponse(entra.safe_next(next) if next != "/"
                                else resume_path_for_oid(claims["oid"]),
                                status_code=303)
    _set_session(response, claims["oid"])
    return response


def resume_path_for_oid(entra_oid: str) -> str:
    learner = db.one("SELECT id FROM learner WHERE entra_oid = %s", (entra_oid,))
    return resume_path(learner["id"]) if learner else "/"

@app.get("/auth/logout", include_in_schema=False)
def sign_out() -> Response:
    """Sign out here AND at Microsoft.

    Clearing only this cookie leaves the Microsoft session intact, so the next
    click on "sign in" silently signs the same person straight back in. On a
    shared machine that is not a sign-out at all, whatever the button said.
    """
    target = "/"
    if entra.configured():
        root = settings.entra_redirect_uri.split("/auth/")[0] or "/"
        target = ("%s/oauth2/v2.0/logout?post_logout_redirect_uri=%s"
                  % (entra.authority(), quote(root, safe="")))
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
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
