"""Who was supposed to take the course, and what became of them.

    python -m server.roster --module security-awareness-essentials --csv hr.csv
    python -m server.roster --module security-awareness-essentials --list
    python -m server.roster --module security-awareness-essentials --remove a@b.c

THE DENOMINATOR WAS THE PEOPLE WHO TURNED UP. `reporting.summary` divided by
`(SELECT count(*) FROM learner)`, and a learner row is created when somebody
SIGNS IN and nowhere else. "Never opened it" therefore meant "signed in, and
never opened it". A person who ignored the training entirely appeared in
neither the numerator nor the denominator: not as untrained, but not at all.

This product exists because "94% of staff are trained" only measures that
people reached the last page. A percentage over the people who showed up is
weaker again, and it is the one that gets pasted into a board pack.

RECONCILIATION REPORTS BOTH ITS FAILURES. Matching is on the email address,
which `learner` itself documents as the weak key — a display attribute in Entra
that changes on marriage, transfer or rebrand. An HR export is nonetheless the
only file an administrator can actually produce, and it is keyed on email. So
rather than pretend the match is sound, this module returns two lists: roster
entries that matched no learner, and learners with results who are on no
roster. A person whose address changed appears once in each, which is a puzzle
somebody can solve. A silent "not started" is not.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from server import cycles, db

#: Column names accepted in an uploaded CSV, lower-cased and stripped. Several
#: spellings each, because an HR export is whatever the HR system emits and
#: making somebody rename columns to match us is a step at which they give up.
FIELDS = {
    "email": ("email", "email address", "e-mail", "mail", "upn",
              "userprincipalname", "user principal name", "work email"),
    "display_name": ("name", "display name", "displayname", "full name",
                     "employee name"),
    "department": ("department", "dept", "team", "division", "cost centre",
                   "cost center"),
    "person_ref": ("employee id", "employee number", "payroll", "payroll id",
                   "payroll number", "person id", "staff number", "person_ref"),
}


def _map_columns(header: Sequence[str]) -> Dict[str, int]:
    seen = {(h or "").strip().lower(): i for i, h in enumerate(header)}
    out: Dict[str, int] = {}
    for field, spellings in FIELDS.items():
        for spelling in spellings:
            if spelling in seen:
                out[field] = seen[spelling]
                break
    return out


def read_csv(path: Path) -> List[Dict[str, str]]:
    """The supplied file as roster rows, or a refusal that says what was wrong.

    A partly-parsed roster is worse than none: it would silently shrink the
    denominator, which is the exact failure this whole module exists to fix. So
    a file without a recognisable email column raises rather than importing the
    rows it could understand.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError("%s is empty." % path)

    columns = _map_columns(rows[0])
    if "email" not in columns:
        raise ValueError(
            "%s has no email column. Looked for: %s.\n"
            "  Found: %s"
            % (path, ", ".join(FIELDS["email"]),
               ", ".join(c.strip() for c in rows[0] if c.strip()) or "(nothing)"))

    out: List[Dict[str, str]] = []
    for lineno, row in enumerate(rows[1:], start=2):
        def value(field: str) -> str:
            index = columns.get(field)
            return (row[index].strip() if index is not None
                    and index < len(row) else "")

        email = value("email")
        if not email:
            continue                      # a blank line, or a trailing comma
        if "@" not in email:
            raise ValueError(
                "%s line %d: %r is not an email address. Nothing was imported."
                % (path, lineno, email))
        out.append({"email": email,
                    "display_name": value("display_name"),
                    "department": value("department"),
                    "person_ref": value("person_ref")})
    if not out:
        raise ValueError("%s named nobody." % path)
    return out


def load(module_id: int, rows: Iterable[Dict[str, str]], *,
         source: str = "csv", replace: bool = False) -> Dict[str, int]:
    """Upsert the supplied people, and report what changed.

    `replace` marks everybody NOT in the file as a leaver — which is what a
    full HR export means and a disaster for a partial one, so it is never the
    default. A leaver is marked, never deleted: they were expected during a
    cycle they may have completed, and erasing that would rewrite what the
    report said at the time.
    """
    supplied = list(rows)
    counts = {"supplied": len(supplied), "added": 0, "updated": 0,
              "restored": 0, "removed": 0}

    with db.connection() as conn:
        for row in supplied:
            existing = conn.execute(
                "SELECT id, removed_at FROM roster_entry "
                "WHERE module_id = %s AND lower(email) = lower(%s)",
                (module_id, row["email"])).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO roster_entry (module_id, email, display_name,"
                    " department, person_ref, source) VALUES (%s,%s,%s,%s,%s,%s)",
                    (module_id, row["email"], row.get("display_name", ""),
                     row.get("department", ""), row.get("person_ref", ""),
                     source))
                counts["added"] += 1
            else:
                if existing["removed_at"] is not None:
                    counts["restored"] += 1
                else:
                    counts["updated"] += 1
                conn.execute(
                    "UPDATE roster_entry SET email = %s, display_name = %s,"
                    " department = %s, person_ref = %s, source = %s,"
                    " removed_at = NULL WHERE id = %s",
                    (row["email"], row.get("display_name", ""),
                     row.get("department", ""), row.get("person_ref", ""),
                     source, existing["id"]))

        if replace:
            addresses = [r["email"].lower() for r in supplied]
            gone = conn.execute(
                "UPDATE roster_entry SET removed_at = now() "
                "WHERE module_id = %s AND removed_at IS NULL "
                "  AND lower(email) <> ALL(%s) RETURNING id",
                (module_id, addresses)).fetchall()
            counts["removed"] = len(gone)
        conn.commit()
    return counts


def remove(module_id: int, email: str) -> bool:
    with db.connection() as conn:
        row = conn.execute(
            "UPDATE roster_entry SET removed_at = now() "
            "WHERE module_id = %s AND lower(email) = lower(%s) "
            "  AND removed_at IS NULL RETURNING id",
            (module_id, email)).fetchone()
        conn.commit()
    return row is not None


# --------------------------------------------------------------------------- #
#  Reconciliation                                                             #
# --------------------------------------------------------------------------- #

def present(module_id: int) -> bool:
    """Is there a roster at all? The report says which denominator it used, and
    it can only do that if it knows."""
    return bool(db.one(
        "SELECT 1 AS yes FROM roster_entry "
        "WHERE module_id = %s AND removed_at IS NULL LIMIT 1", (module_id,)))


#: The join between an expectation and a person. `upn` as well as `email`
#: because Entra sign-in stores both and an HR export may carry either; both
#: lower-cased because an HR system and a directory disagree about
#: capitalisation far more often than about identity.
_MATCH = ("LEFT JOIN learner l ON lower(l.email) = lower(r.email) "
          "                    OR (l.upn <> '' AND lower(l.upn) = lower(r.email))")


def expected(module_id: int, cycle_id: Optional[int] = None
             ) -> List[Dict[str, Any]]:
    """One row per person who was supposed to take this, in this cycle.

    `signed_in` is the state the old report could not express at all: false
    means no learner row exists for that address, which is to say the person
    has never once signed in. It is not the same as "signed in and never opened
    it", and collapsing the two is what made the figure meaningless.
    """
    if cycle_id is None:
        cycle_id = cycles.current_id(module_id)
    return db.query(
        f"""
        SELECT r.email, r.display_name AS roster_name, r.department,
               r.person_ref,
               l.id AS learner_id,
               (l.id IS NOT NULL)             AS signed_in,
               l.display_name                 AS learner_name,
               e.started_at, e.completed_at,
               COALESCE(e.furthest_ordinal, 0) AS furthest_ordinal,
               c.serial AS certificate, c.issued_at
        FROM roster_entry r
        {_MATCH}
        LEFT JOIN enrolment e ON e.learner_id = l.id
             AND e.module_id = %(module)s
             AND e.cycle_id IS NOT DISTINCT FROM %(cycle)s
        LEFT JOIN certificate c ON c.learner_id = l.id
             AND c.module_id = %(module)s
             AND c.cycle_id IS NOT DISTINCT FROM %(cycle)s
        WHERE r.module_id = %(module)s AND r.removed_at IS NULL
        ORDER BY r.department, COALESCE(NULLIF(r.display_name, ''), r.email)
        """, {"module": module_id, "cycle": cycle_id})


def off_roster(module_id: int, cycle_id: Optional[int] = None
               ) -> List[Dict[str, Any]]:
    """People with results this cycle who are on no roster.

    HALF OF EVERY MATCHING FAILURE LANDS HERE, which is why it is reported
    rather than filtered away. A person whose address changed since the export
    shows up once here and once in the unmatched list, and an administrator can
    put the two together. Silently dropping them would leave the roster looking
    tidy and the figure wrong.

    It is also the honest home for somebody who took the training and should
    have been on the roster: they did the work, and a report that cannot show
    it is not a better report for being neat.
    """
    if cycle_id is None:
        cycle_id = cycles.current_id(module_id)
    return db.query(
        """
        SELECT l.id AS learner_id, l.email, l.display_name, l.department,
               e.started_at, e.completed_at, c.serial AS certificate
        FROM learner l
        LEFT JOIN enrolment e ON e.learner_id = l.id
             AND e.module_id = %(module)s
             AND e.cycle_id IS NOT DISTINCT FROM %(cycle)s
        LEFT JOIN certificate c ON c.learner_id = l.id
             AND c.module_id = %(module)s
             AND c.cycle_id IS NOT DISTINCT FROM %(cycle)s
        WHERE (e.id IS NOT NULL OR c.id IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM roster_entry r
               WHERE r.module_id = %(module)s AND r.removed_at IS NULL
                 AND (lower(r.email) = lower(l.email)
                      OR (l.upn <> '' AND lower(r.email) = lower(l.upn))))
        ORDER BY l.department, l.display_name, l.email
        """, {"module": module_id, "cycle": cycle_id})


def stats(module_id: int, cycle_id: Optional[int] = None
          ) -> Optional[Dict[str, Any]]:
    """The headline over the right population, or None when there is no roster.

    None rather than zeroes. A roster nobody has imported and a roster naming
    nobody are different states, and only one of them means the report should
    stop quoting a percentage.
    """
    if not present(module_id):
        return None
    rows = expected(module_id, cycle_id)
    unmatched = [r for r in rows if not r["signed_in"]]
    return {
        "expected": len(rows),
        # The number the old report could not produce, and the only one on this
        # screen that answers "who has not done it".
        "never_signed_in": len(unmatched),
        "signed_in_not_started": sum(
            1 for r in rows if r["signed_in"] and r["started_at"] is None),
        "started_not_passed": sum(
            1 for r in rows if r["started_at"] is not None
            and r["certificate"] is None),
        "passed": sum(1 for r in rows if r["certificate"]),
        "off_roster": len(off_roster(module_id, cycle_id)),
        "never_signed_in_people": [
            {"email": r["email"], "display_name": r["roster_name"],
             "department": r["department"]} for r in unmatched],
    }


# --------------------------------------------------------------------------- #
#  Command line                                                               #
# --------------------------------------------------------------------------- #

def _module(slug: str) -> Dict[str, Any]:
    row = db.one("SELECT id, slug, title FROM module WHERE slug = %s", (slug,))
    if not row:
        known = [m["slug"] for m in db.query("SELECT slug FROM module ORDER BY slug")]
        raise SystemExit("no module %r. Known: %s"
                         % (slug, ", ".join(known) or "(none)"))
    return row


def main(argv: Optional[Sequence[str]] = None) -> int:
    from server.config import settings

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--module", required=True, help="module slug")
    parser.add_argument("--csv", type=Path, help="an HR export to import")
    parser.add_argument("--replace", action="store_true",
                        help="mark everybody NOT in the file as a leaver. Only "
                             "for a FULL export — on a partial file it removes "
                             "everybody it does not mention.")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report, change nothing")
    parser.add_argument("--remove", metavar="EMAIL",
                        help="mark one person as no longer expected")
    parser.add_argument("--list", action="store_true",
                        help="the roster and what became of each person")
    args = parser.parse_args(argv)

    settings.validate()
    module = _module(args.module)

    if args.remove:
        if remove(module["id"], args.remove):
            print("%s is no longer expected to take %s."
                  % (args.remove, module["slug"]))
        else:
            print("%s was not on the roster for %s."
                  % (args.remove, module["slug"]))
        return 0

    if args.csv:
        rows = read_csv(args.csv)
        if args.dry_run:
            print("%d row(s) parsed from %s. Nothing was written."
                  % (len(rows), args.csv))
            for row in rows[:10]:
                print("  %-40s %-24s %s"
                      % (row["email"], row["display_name"], row["department"]))
            if len(rows) > 10:
                print("  ... and %d more" % (len(rows) - 10))
            return 0
        counts = load(module["id"], rows, replace=args.replace)
        print("%s: %d supplied — %d added, %d updated, %d restored, %d marked "
              "as leavers." % (module["slug"], counts["supplied"],
                               counts["added"], counts["updated"],
                               counts["restored"], counts["removed"]))
        if not args.replace:
            print("  (--replace would also mark everybody absent from the file "
                  "as a leaver.)")

    if args.list or not args.csv:
        figures = stats(module["id"])
        if figures is None:
            print("No roster for %s. The report is counting the people who "
                  "have signed in." % module["slug"])
            return 0
        print("%s — %d expected: %d passed, %d started, %d signed in but not "
              "started, %d never signed in."
              % (module["slug"], figures["expected"], figures["passed"],
                 figures["started_not_passed"],
                 figures["signed_in_not_started"], figures["never_signed_in"]))
        if figures["off_roster"]:
            print("  %d person(s) have results and are on no roster — see the "
                  "reporting page." % figures["off_roster"])
        for row in expected(module["id"]):
            state = ("passed" if row["certificate"]
                     else "started" if row["started_at"]
                     else "signed in" if row["signed_in"]
                     else "NEVER SIGNED IN")
            print("  %-40s %-20s %s"
                  % (row["email"], row["department"] or "-", state))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
