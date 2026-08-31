"""Training cycles: the periods a course has to be completed within.

Awareness training is not a thing somebody does once. The regulations this
course teaches require it periodically, so "have they passed" is the wrong
question — the right one is "have they passed **this time round**".

A cycle is a named period with an opening date and an optional date it is due
by. The current cycle is the most recent one that has opened. Somebody holding
a certificate for it is done; everybody else has the course open to them,
including everybody who passed last year.

**A deployment with no cycles behaves exactly as it did before**, which is
one-and-done. That is deliberate: the feature is something an administrator
turns on by opening the first cycle, not something that changes underneath a
portal already in use.

**Opening a cycle is the whole administrative act.** There is no closing: a
cycle ends when the next one opens. Nothing is deleted and no certificate is
invalidated — last year's is still the record of last year, which is what an
auditor is asking to see when they ask about last year.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server import db


def current(module_id: int, now: Optional[datetime] = None
            ) -> Optional[Dict[str, Any]]:
    """The cycle in force for this module, or None if there are none.

    The most recently OPENED, not the most recently created: a cycle can be
    set up in advance with a date in the future, and it must not take effect
    until that date arrives.
    """
    return db.one(
        """
        SELECT id, module_id, name, opens_at, due_at
        FROM training_cycle
        WHERE module_id = %s AND opens_at <= %s
        ORDER BY opens_at DESC, id DESC LIMIT 1
        """, (module_id, now or datetime.now(timezone.utc)))


def current_id(module_id: int) -> Optional[int]:
    cycle = current(module_id)
    return cycle["id"] if cycle else None


def listing(module_id: int) -> List[Dict[str, Any]]:
    """Every cycle for a module, newest first, with how many have passed it."""
    return db.query(
        """
        SELECT c.id, c.name, c.opens_at, c.due_at,
               (SELECT count(*) FROM certificate x
                 WHERE x.cycle_id = c.id)                    AS passed,
               c.opens_at <= now()                           AS open
        FROM training_cycle c
        WHERE c.module_id = %s
        ORDER BY c.opens_at DESC, c.id DESC
        """, (module_id,))


def open_cycle(module_id: int, name: str, opens_at: Optional[datetime] = None,
               due_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Begin a new cycle. Everybody who passed a previous one is due again.

    Certificates already issued on or after this cycle's opening date are
    adopted into it, so opening a cycle backdated to the start of the year
    does not ask the people who have already done this year's training to do
    it twice.
    """
    cycle = db.one(
        """
        INSERT INTO training_cycle (module_id, name, opens_at, due_at)
        VALUES (%s, %s, COALESCE(%s, now()), %s)
        RETURNING id, module_id, name, opens_at, due_at
        """, (module_id, name, opens_at, due_at))

    adopted = db.query(
        """
        UPDATE certificate SET cycle_id = %(cycle)s
        WHERE module_id = %(module)s AND cycle_id IS NULL
          AND issued_at >= (SELECT opens_at FROM training_cycle
                             WHERE id = %(cycle)s)
        RETURNING id
        """, {"cycle": cycle["id"], "module": module_id})
    cycle["adopted"] = len(adopted)
    return cycle


def standing(learner_id: int, module_id: int) -> Dict[str, Any]:
    """Where somebody stands: the cycle in force, and whether they have met it.

    One function, so that the API, the reporting view and the command line
    cannot come to three different answers about whether a person is due.
    """
    cycle = current(module_id)
    held = db.one(
        """
        SELECT c.serial, c.issued_at, c.score, c.out_of, c.name_printed,
               c.cycle_id, a.attempt_no
        FROM certificate c JOIN attempt a ON a.id = c.attempt_id
        WHERE c.learner_id = %s AND c.module_id = %s
          AND c.cycle_id IS NOT DISTINCT FROM %s
        ORDER BY c.issued_at LIMIT 1
        """, (learner_id, module_id, cycle["id"] if cycle else None))

    # What they hold from an earlier period, for a screen that wants to say
    # "you did this last year, and it is due again".
    previous = None
    if cycle and not held:
        previous = db.one(
            """
            SELECT c.serial, c.issued_at, t.name AS cycle_name
            FROM certificate c
            LEFT JOIN training_cycle t ON t.id = c.cycle_id
            WHERE c.learner_id = %s AND c.module_id = %s
            ORDER BY c.issued_at DESC LIMIT 1
            """, (learner_id, module_id))

    return {"cycle": cycle, "held": held, "previous": previous,
            "due": bool(cycle) and not held}
