"""Open and inspect the training cycles a course has to be completed within.

    python -m server.cycle --list
    python -m server.cycle --open "2027 annual refresher" --due 2027-03-31
    python -m server.cycle --open "2027 annual refresher" --opens 2027-01-01

From the shell, like `server.grant` and `server.account`. Opening a cycle asks
the whole organisation to do the training again, which is not something an
application should be able to decide for itself.

**Nothing is deleted and no certificate is invalidated.** Last year's remains
the record of last year — which is what an auditor is asking to see when they
ask about last year. What changes is that the course opens again to everybody
who has not passed *this* cycle.

**A module with no cycles behaves as it always has**: passing closes it for
good. The first `--open` is the moment that changes, and it says how many
people it has just made due.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timezone

from server import cycles, db
from server.config import settings

SLUG = "security-awareness-essentials"


def _when(text: str, end_of_day: bool = False) -> datetime:
    """A date on the command line, as an instant.

    Dates rather than timestamps, because nobody types an hour into a
    training deadline. A due date means the END of that day — "due by the
    31st" is not "due at midnight as the 31st begins".
    """
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("dates look like 2027-03-31; got %r" % text)
    moment = time(23, 59, 59) if end_of_day else time(0, 0)
    return datetime.combine(day, moment, tzinfo=timezone.utc)


def _module(slug: str):
    module = db.one("SELECT id, title FROM module WHERE slug = %s", (slug,))
    if not module:
        raise SystemExit("no module with the slug %r." % slug)
    return module


def show(module) -> int:
    rows = cycles.listing(module["id"])
    if not rows:
        print("%s has no training cycles." % module["title"])
        print()
        print("It behaves as it always has: passing closes the course for")
        print("good. Open the first cycle when the training becomes something")
        print("people have to do again.")
        return 0

    now = cycles.current(module["id"])
    print("%-28s %-12s %-12s %8s" % ("cycle", "opens", "due", "passed"))
    for row in rows:
        mark = " <- in force" if now and row["id"] == now["id"] else \
               " (not yet open)" if not row["open"] else ""
        print("%-28s %-12s %-12s %8d%s"
              % (row["name"][:28], row["opens_at"].date(),
                 row["due_at"].date() if row["due_at"] else "—",
                 row["passed"], mark))

    outstanding = db.one(
        """
        SELECT count(*) AS n FROM learner l
        WHERE NOT EXISTS (SELECT 1 FROM certificate c
                           WHERE c.learner_id = l.id
                             AND c.module_id = %s AND c.cycle_id = %s)
        """, (module["id"], now["id"] if now else None))
    if now:
        print()
        print("%d of %d people have still to complete %s."
              % (outstanding["n"],
                 db.one("SELECT count(*) AS n FROM learner")["n"], now["name"]))
    return 0


def open_it(module, args) -> int:
    name = args.open.strip()
    if not name:
        raise SystemExit("a cycle needs a name — it is what the report calls it.")
    if db.one("SELECT id FROM training_cycle WHERE module_id = %s AND name = %s",
              (module["id"], name)):
        raise SystemExit("%s already has a cycle called %r." % (module["title"], name))

    opens = _when(args.opens) if args.opens else None
    due = _when(args.due, end_of_day=True) if args.due else None
    if opens and due and due < opens:
        raise SystemExit("the due date is before the cycle opens.")

    was_due = db.one(
        "SELECT count(DISTINCT learner_id) AS n FROM certificate "
        "WHERE module_id = %s", (module["id"],))["n"]

    cycle = cycles.open_cycle(module["id"], name, opens, due)

    print("Opened %r for %s." % (cycle["name"], module["title"]))
    print("  opens: %s" % cycle["opens_at"].date())
    print("  due:   %s" % (cycle["due_at"].date() if cycle["due_at"] else "no date set"))
    if cycle["adopted"]:
        print()
        print("  %d certificate(s) issued on or after that date were adopted"
              % cycle["adopted"])
        print("  into this cycle, so those people are not asked to do it twice.")
    print()
    if cycle["opens_at"] > datetime.now(timezone.utc):
        print("It is dated in the future and is not in force yet.")
    else:
        print("%d people who had already passed are now due again."
              % max(0, was_due - cycle["adopted"]))
        print("Their certificates are untouched and still downloadable.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", default=SLUG)
    parser.add_argument("--open", metavar="NAME",
                        help='what to call it, e.g. "2027 annual refresher"')
    parser.add_argument("--opens", metavar="YYYY-MM-DD",
                        help="when it takes effect (default: now)")
    parser.add_argument("--due", metavar="YYYY-MM-DD",
                        help="when everybody should have finished by")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    settings.validate()
    module = _module(args.module)
    return show(module) if args.list or not args.open else open_it(module, args)


if __name__ == "__main__":
    sys.exit(main())
