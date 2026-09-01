"""What the training actually shows, for the people who have to act on it.

This is the screen the whole schema was shaped around, and it has one job that
is easy to get wrong: NOT to produce the number everybody wants.

The number everybody wants is "94% trained". It is a single figure, it fits on
a slide, and it is the reason awareness training has the reputation it has —
because it reports that people reached the last page and is then read as
evidence that they can spot a phish. Those are different claims and this
module never merges them. Completion and learning appear side by side, always,
even when that makes the picture less flattering.

Three further honesties are built in rather than left to whoever reads it:

A RATE FROM A HANDFUL OF ANSWERS IS NOT A RATE. "100% correct" from three
attempts is noise wearing the costume of a statistic, and it is exactly the
kind of false confidence this product exists to avoid. Below a floor, the
proportion is not reported at all — the count is shown instead.

A QUESTION EVERYBODY GETS RIGHT MEASURES NOTHING. It cannot separate a person
who understands from one who does not, so a high score on it is not evidence
of awareness. Those questions are surfaced as a problem with the QUESTION, not
celebrated as a result.

WHERE PEOPLE STOP IS A FACT ABOUT THE MATERIAL. Somebody who opened the course
and got to slide three has told you something. Rounding them in with the people
who never opened it throws that away, so the stall points are reported
separately and by slide.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from server import cycles, db, roster

#: Below this many answers, a correct rate is not reported. Small samples are
#: where confident-looking nonsense comes from, and a report that says "100%"
#: over three answers will be quoted as if it meant something.
MIN_ANSWERS = 20

#: At or above this correct rate, a question has stopped telling people apart.
#: It is not a good question that everybody passes; it is an unasked question.
NOT_DISCRIMINATING = 0.95

#: At or below this, either the material failed to teach it or the question is
#: broken. Either way it is worth a look, and it is worth looking at the SLIDE
#: first — which is what `teaches` is for.
MATERIAL_FAILED = 0.40

#: Faster than this is a guess or a remembered position, not knowledge. Client
#: reported, and acceptable here because it judges the QUESTION, not the person.
GUESS_MS = 2000


def summary(module_id: int) -> Dict[str, Any]:
    """The headline, deliberately as several numbers rather than one.

    All of it for the cycle in force. "94% trained" is bad enough without it
    also meaning "at some point in the last four years" — the question an
    auditor asks is about a period, and a figure that quietly spans every
    period there has ever been answers a question nobody asked.
    """
    cycle = cycles.current(module_id)
    cycle_id = cycle["id"] if cycle else None

    row = db.one(
        """
        SELECT
          (SELECT count(*) FROM learner) AS people,
          count(e.id)                                  AS enrolled,
          count(e.id) FILTER (WHERE e.started_at IS NOT NULL)   AS opened,
          count(e.id) FILTER (WHERE e.completed_at IS NOT NULL) AS reached_end,
          count(e.id) FILTER (WHERE e.completed_at IS NULL
                                AND e.furthest_ordinal > 0)     AS stopped_partway
        FROM enrolment e
        WHERE e.module_id = %s AND e.cycle_id IS NOT DISTINCT FROM %s
        """, (module_id, cycle_id))

    passing = db.one(
        """
        SELECT count(*) AS passed,
               count(*) FILTER (WHERE first_go) AS passed_first_time
        FROM (
            SELECT c.learner_id,
                   bool_or(a.attempt_no = 1) AS first_go
            FROM certificate c JOIN attempt a ON a.id = c.attempt_id
            WHERE c.module_id = %s AND c.cycle_id IS NOT DISTINCT FROM %s
            GROUP BY c.learner_id
        ) AS holders
        """, (module_id, cycle_id))

    people = row["people"] or 0
    # WHO WAS SUPPOSED TO TAKE IT, when somebody has said. None when nobody
    # has, and the screen then states out loud that it is counting sign-ins —
    # see server/roster.py for why that denominator was the wrong one.
    expected = roster.stats(module_id, cycle_id)
    return {
        "roster": expected,
        # Named, so nobody reads a figure off this screen without knowing
        # which period it is about.
        "cycle": cycle,
        "overdue": bool(cycle and cycle["due_at"]
                        and cycle["due_at"] < datetime.now(timezone.utc)),
        # These two remain the sign-in population, unchanged and now
        # explicitly labelled as such by the screen. They are not replaced by
        # the roster figures: "signed in and never opened it" is a real state
        # with its own remedy, and it is not the same as never having appeared.
        "people": people,
        "never_opened": people - row["opened"],
        "opened": row["opened"],
        "stopped_partway": row["stopped_partway"],
        "reached_end": row["reached_end"],
        "passed": passing["passed"],
        # The figure that means the most and gets quoted the least: a pass on
        # the third go is not the same evidence as a pass on the first.
        "passed_first_time": passing["passed_first_time"],
    }


def people(module_id: int) -> List[Dict[str, Any]]:
    """One row per person, for the cycle in force.

    Somebody who passed last year and has not started this year shows as not
    started, which is the truth about this cycle and the only useful thing to
    put in front of whoever has to chase them.

    AND, WHERE A ROSTER EXISTS, one row per person who never signed in at all.
    This list is what somebody works down when they have to chase; it was built
    from the `learner` table, so the people who ignored the training entirely
    were the only ones not on it. `signed_in` is false for those rows and every
    result column is null — not zero, because nothing was measured.
    """
    rows = _people_who_signed_in(module_id)
    if not roster.present(module_id):
        return rows

    known = {r["email"].lower() for r in rows if r["email"]}
    absent = [
        {"id": None, "email": entry["email"],
         "display_name": entry["roster_name"] or entry["email"],
         "department": entry["department"],
         "started_at": None, "completed_at": None, "furthest_ordinal": 0,
         "attempts": 0, "latest_score": None, "out_of": None,
         "certificate": None, "issued_at": None, "passed_on_attempt": None,
         "signed_in": False, "on_roster": True}
        for entry in roster.expected(module_id, cycles.current_id(module_id))
        if not entry["signed_in"] and entry["email"].lower() not in known]

    on_roster = {e["email"].lower() for e in roster.expected(module_id)}
    for row in rows:
        row["signed_in"] = True
        # False here is the other half of a matching failure — somebody with
        # results whose address is on no roster row. Shown, never filtered.
        row["on_roster"] = (row["email"] or "").lower() in on_roster

    return sorted(rows + absent,
                  key=lambda r: ((r["department"] or "").lower(),
                                 (r["display_name"] or "").lower(),
                                 (r["email"] or "").lower()))


def _people_who_signed_in(module_id: int) -> List[Dict[str, Any]]:
    return db.query(
        """
        SELECT l.id, l.email, l.display_name, l.department,
               e.started_at, e.completed_at,
               COALESCE(e.furthest_ordinal, 0) AS furthest_ordinal,
               -- This cycle's attempts. An attempt from last year is not
               -- a retake of this year's check.
               (SELECT count(*) FROM attempt a
                 WHERE a.learner_id = l.id AND a.module_id = %(module)s
                   AND a.cycle_id IS NOT DISTINCT FROM %(cycle)s
                   AND a.finished_at IS NOT NULL)          AS attempts,
               (SELECT a.score FROM attempt a
                 WHERE a.learner_id = l.id AND a.module_id = %(module)s
                   AND a.cycle_id IS NOT DISTINCT FROM %(cycle)s
                   AND a.finished_at IS NOT NULL
                 ORDER BY a.attempt_no DESC LIMIT 1)        AS latest_score,
               (SELECT a.out_of FROM attempt a
                 WHERE a.learner_id = l.id AND a.module_id = %(module)s
                   AND a.cycle_id IS NOT DISTINCT FROM %(cycle)s
                   AND a.finished_at IS NOT NULL
                 ORDER BY a.attempt_no DESC LIMIT 1)        AS out_of,
               c.serial   AS certificate,
               c.issued_at,
               (SELECT a.attempt_no FROM attempt a
                 WHERE a.id = c.attempt_id)                 AS passed_on_attempt
        FROM learner l
        LEFT JOIN enrolment e
               ON e.learner_id = l.id AND e.module_id = %(module)s
              AND e.cycle_id IS NOT DISTINCT FROM %(cycle)s
        LEFT JOIN certificate c
               ON c.learner_id = l.id AND c.module_id = %(module)s
              AND c.cycle_id IS NOT DISTINCT FROM %(cycle)s
        ORDER BY l.department, l.display_name, l.email
        """, {"module": module_id, "cycle": cycles.current_id(module_id)})


def questions(module_id: int) -> List[Dict[str, Any]]:
    """How each question is behaving. This is the portal reporting on itself.

    `verdict` is the whole point of the screen: it says whether the question is
    worth asking, not whether the people are any good.
    """
    rows = db.query(
        """
        SELECT s.question_id, s.ordinal, s.prompt, q.teaches,
               s.answered, s.correct, s.correct_rate, s.answered_under_2s,
               l.title AS teaches_title
        FROM question_stat s
        JOIN question q ON q.id = s.question_id
        LEFT JOIN lesson l
               ON l.module_id = q.module_id AND l.ordinal = q.teaches
        WHERE q.module_id = %s AND NOT q.retired
        ORDER BY s.answered DESC, s.ordinal
        """, (module_id,))

    for row in rows:
        answered = row["answered"] or 0
        rate = float(row["correct_rate"]) if row["correct_rate"] is not None else None
        if answered == 0:
            row["verdict"] = "never asked"
        elif answered < MIN_ANSWERS:
            # Reported as a count, not a proportion. See MIN_ANSWERS.
            row["verdict"] = "too few answers to say"
            rate = None
        elif rate is not None and rate >= NOT_DISCRIMINATING:
            row["verdict"] = "everybody gets this right — it measures nothing"
        elif rate is not None and rate <= MATERIAL_FAILED:
            row["verdict"] = "most people get this wrong — look at the slide"
        else:
            row["verdict"] = "discriminating"
        row["correct_rate"] = rate
        row["guessed"] = row.pop("answered_under_2s")
    return rows


def slides(module_id: int) -> List[Dict[str, Any]]:
    """Where people stop. A slide that many people never get past is a fact
    about that slide."""
    return db.query(
        """
        SELECT l.ordinal, l.title,
               count(e.id) FILTER (WHERE e.completed_at IS NULL
                                     AND e.furthest_ordinal = l.ordinal)
                   AS stopped_here,
               count(e.id) FILTER (WHERE e.furthest_ordinal >= l.ordinal)
                   AS reached
        FROM lesson l
        LEFT JOIN enrolment e ON e.module_id = l.module_id
        WHERE l.module_id = %s
        GROUP BY l.ordinal, l.title
        ORDER BY l.ordinal
        """, (module_id,))


def departments(module_id: int) -> List[Dict[str, Any]]:
    return db.query(
        """
        SELECT COALESCE(NULLIF(l.department, ''), 'Not recorded') AS department,
               count(*) AS people,
               count(e.id) FILTER (WHERE e.completed_at IS NOT NULL) AS reached_end,
               count(c.id) AS passed
        FROM learner l
        LEFT JOIN enrolment e
               ON e.learner_id = l.id AND e.module_id = %(module)s
        LEFT JOIN certificate c
               ON c.learner_id = l.id AND c.module_id = %(module)s
        GROUP BY 1 ORDER BY 1
        """, {"module": module_id})


def delivery(module_id: int) -> Dict[str, Any]:
    """Certificates issued, and whether they actually went anywhere.

    An email that bounced and one that was never attempted look identical from
    the outside, and both look like a certificate that was sent. This is where
    that difference becomes visible to somebody who can act on it.
    """
    row = db.one(
        """
        SELECT count(*) AS issued,
               count(*) FILTER (WHERE emailed_at IS NOT NULL) AS emailed,
               count(*) FILTER (WHERE emailed_at IS NULL
                                  AND email_error <> '')      AS failed,
               count(*) FILTER (WHERE emailed_at IS NULL
                                  AND email_error = '')       AS not_attempted
        FROM certificate WHERE module_id = %s
        """, (module_id,))
    row["failures"] = db.query(
        """
        SELECT c.serial, l.email, c.email_error, c.issued_at
        FROM certificate c JOIN learner l ON l.id = c.learner_id
        WHERE c.module_id = %s AND c.emailed_at IS NULL AND c.email_error <> ''
        ORDER BY c.issued_at DESC LIMIT 50
        """, (module_id,))
    return row
