"""Who was supposed to take this.

THE DEFECT. `reporting.summary` divided by `(SELECT count(*) FROM learner)`,
and a learner row is created when somebody SIGNS IN — in `auth.upsert_learner`
and `account.create`, and nowhere else. So the report's "never opened it"
meant "signed in, and never opened it". Somebody who ignored the training
entirely was in neither the numerator nor the denominator: they did not read as
untrained, they did not read at all.

That matters most for exactly the people this portal went out of its way to
serve. The password sign-in exists, in the schema's own words, for
"contractors, shift staff on shared OT terminals, and site engineers who have a
payroll number and no mailbox" — the population least likely to have signed in
unprompted, and the population the old figure could not see.

The rule these tests hold: **never signed in is a state, not an absence.** It
is not the same as "signed in and never started", and a report that collapses
the two is quoting a percentage of the people who turned up.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import needs_db

pytestmark = needs_db


def _module_id():
    from server import db
    return db.one("SELECT id FROM module ORDER BY id LIMIT 1")["id"]


def _learner(email, name="A Person", department="Ops", upn=None):
    from server import auth
    return auth.upsert_learner(entra_oid=str(uuid.uuid4()), email=email,
                               upn=upn if upn is not None else email,
                               display_name=name, department=department)


def _rows(*emails):
    return [{"email": e, "display_name": e.split("@")[0].title(),
             "department": "Ops", "person_ref": ""} for e in emails]


# ── importing ────────────────────────────────────────────────────────────────

def test_a_roster_can_be_loaded_and_counted(clean):
    from server import roster
    module = _module_id()
    counts = roster.load(module, _rows("a@x.com", "b@x.com", "c@x.com"))
    assert counts == {"supplied": 3, "added": 3, "updated": 0,
                      "restored": 0, "removed": 0}
    assert roster.present(module)
    assert roster.stats(module)["expected"] == 3


def test_reimporting_the_same_person_updates_rather_than_duplicates(clean):
    """Two rows for one person would inflate the denominator, which is the
    failure this module exists to fix, arriving through its own front door."""
    from server import roster
    module = _module_id()
    roster.load(module, _rows("a@x.com"))
    counts = roster.load(module, [{"email": "A@X.com", "display_name": "A Person",
                                   "department": "Field", "person_ref": "42"}])
    assert counts["added"] == 0 and counts["updated"] == 1
    rows = roster.expected(module)
    assert len(rows) == 1, "a differently-cased address created a second person"
    assert rows[0]["department"] == "Field", "the update did not take"
    assert rows[0]["person_ref"] == "42"


def test_a_partial_file_does_not_silently_remove_everybody_else(clean):
    """`--replace` is what a full export means and a disaster for a partial
    one, so it is never what happens by default."""
    from server import roster
    module = _module_id()
    roster.load(module, _rows("a@x.com", "b@x.com"))
    counts = roster.load(module, _rows("a@x.com"))
    assert counts["removed"] == 0
    assert roster.stats(module)["expected"] == 2


def test_replace_marks_the_absent_as_leavers_without_deleting_them(clean):
    from server import db, roster
    module = _module_id()
    roster.load(module, _rows("a@x.com", "b@x.com"))
    counts = roster.load(module, _rows("a@x.com"), replace=True)

    assert counts["removed"] == 1
    assert roster.stats(module)["expected"] == 1
    # Marked, not deleted: they were expected during a cycle they may have
    # completed, and erasing that rewrites what the report said at the time.
    assert db.one("SELECT count(*) AS n FROM roster_entry "
                  "WHERE module_id = %s", (module,))["n"] == 2


def test_a_returning_leaver_is_restored_not_duplicated(clean):
    from server import roster
    module = _module_id()
    roster.load(module, _rows("a@x.com", "b@x.com"))
    roster.load(module, _rows("a@x.com"), replace=True)
    counts = roster.load(module, _rows("a@x.com", "b@x.com"))
    assert counts["restored"] == 1 and counts["added"] == 0
    assert roster.stats(module)["expected"] == 2


# ── the state the old report could not express ───────────────────────────────

def test_somebody_who_never_signed_in_is_counted_and_named(clean):
    """The whole point. This person did not exist in the database at all."""
    from server import roster
    module = _module_id()
    _learner("present@x.com")
    roster.load(module, _rows("present@x.com", "absent@x.com"))

    figures = roster.stats(module)
    assert figures["expected"] == 2
    assert figures["never_signed_in"] == 1
    assert [p["email"] for p in figures["never_signed_in_people"]] \
        == ["absent@x.com"]


def test_never_signed_in_is_not_the_same_as_signed_in_and_not_started(clean):
    """Two different failures with two different remedies: one person needs an
    account or a nudge, the other needs reminding to open it."""
    from server import roster
    module = _module_id()
    _learner("here@x.com")
    roster.load(module, _rows("here@x.com", "missing@x.com"))

    figures = roster.stats(module)
    assert figures["never_signed_in"] == 1
    assert figures["signed_in_not_started"] == 1
    assert figures["never_signed_in"] + figures["signed_in_not_started"] == 2


def test_the_roster_matches_on_the_entra_upn_as_well_as_the_email(clean):
    """An HR export may carry either. Matching on one and not the other reports
    somebody who signs in every day as never having signed in."""
    from server import roster
    module = _module_id()
    _learner("j.smith@x.com", upn="jsmith@corp.x.com")
    roster.load(module, _rows("jsmith@corp.x.com"))
    assert roster.stats(module)["never_signed_in"] == 0


def test_matching_ignores_case(clean):
    """An HR export and a directory disagree about capitalisation far more
    often than about identity, and sign-in stores the address as given.

    THE UPN IS DELIBERATELY DIFFERENT HERE. With upn == email the second arm of
    the join can satisfy this test on its own, and a mutation that made the
    EMAIL arm case-sensitive still passed — the test proved the pair worked,
    not the part it names."""
    from server import roster
    module = _module_id()
    _learner("Jane.Doe@X.com", upn="jd-payroll-00417@x.com")
    roster.load(module, _rows("jane.doe@x.com"))
    assert roster.stats(module)["never_signed_in"] == 0


# ── the reconciliation reports its own failures ──────────────────────────────

def test_somebody_with_results_and_no_roster_row_is_reported_not_hidden(clean):
    """HALF OF EVERY MATCHING FAILURE LANDS HERE. A person whose address
    changed since the export appears once as unmatched and once here, and an
    administrator can put the two together. Filtering them away would leave the
    roster tidy and the figure wrong."""
    from server import db, roster
    module = _module_id()
    learner = _learner("new.name@x.com")
    db.execute("INSERT INTO enrolment (learner_id, module_id, started_at) "
               "VALUES (%s,%s, now())", (learner["id"], module))
    roster.load(module, _rows("old.name@x.com"))

    figures = roster.stats(module)
    assert figures["never_signed_in"] == 1, "the old address, unmatched"
    assert figures["off_roster"] == 1, "the new address, with the results"
    assert [r["email"] for r in roster.off_roster(module)] == ["new.name@x.com"]


def test_a_learner_with_no_results_at_all_is_not_reported_as_off_roster(clean):
    """Somebody who signed in once and did nothing is not evidence of a
    matching failure — there is nothing of theirs to have been mislaid."""
    from server import roster
    module = _module_id()
    _learner("idle@x.com")
    roster.load(module, _rows("someone@x.com"))
    assert roster.stats(module)["off_roster"] == 0


# ── no roster is a state of its own ──────────────────────────────────────────

def test_with_no_roster_the_stats_are_none_rather_than_zero(clean):
    """None, so the report can say which denominator it used. Zeroes would let
    a screen print "0 of 0 outstanding", which is a reassuring way of saying
    nothing is known."""
    from server import roster
    assert roster.stats(_module_id()) is None
    assert roster.present(_module_id()) is False


def test_a_roster_emptied_by_departures_is_still_a_roster(clean):
    """Everybody left is a real and different answer from never having
    imported one. It must not silently revert to counting sign-ins."""
    from server import roster
    module = _module_id()
    roster.load(module, _rows("a@x.com"))
    roster.load(module, [], replace=True)
    assert roster.present(module) is False, \
        "an empty roster claims a population it does not have"


# ── the CSV an HR system actually emits ──────────────────────────────────────

def test_a_csv_is_read_by_column_name_in_several_spellings(tmp_path):
    from server import roster
    path = tmp_path / "hr.csv"
    path.write_text(
        "Employee Name,Work Email,Cost Centre,Payroll Number\n"
        "Ada Lovelace,ada@x.com,Engineering,00417\n"
        "Grace Hopper,grace@x.com,Engineering,00418\n",
        encoding="utf-8")
    rows = roster.read_csv(path)
    assert [r["email"] for r in rows] == ["ada@x.com", "grace@x.com"]
    assert rows[0]["display_name"] == "Ada Lovelace"
    assert rows[0]["department"] == "Engineering"
    assert rows[0]["person_ref"] == "00417"


def test_a_csv_with_a_utf8_bom_still_finds_its_first_column(tmp_path):
    """Excel writes one. It attaches to the first header, so `email` becomes
    `\\ufeffemail` and the column vanishes — and the failure is a roster that
    imported nothing rather than an error."""
    from server import roster
    path = tmp_path / "excel.csv"
    path.write_bytes("email,name\na@x.com,A\n".encode("utf-8-sig"))
    assert roster.read_csv(path)[0]["email"] == "a@x.com"


def test_a_file_with_no_email_column_is_refused_rather_than_half_imported(
        tmp_path):
    """A partly-parsed roster silently shrinks the denominator, which is this
    module's own failure mode arriving through its front door."""
    from server import roster
    path = tmp_path / "wrong.csv"
    path.write_text("name,dept\nAda,Engineering\n", encoding="utf-8")
    with pytest.raises(ValueError) as problem:
        roster.read_csv(path)
    assert "no email column" in str(problem.value)
    # And says what it looked for, so the fix is renaming one column rather
    # than guessing.
    assert "work email" in str(problem.value)


def test_one_bad_address_stops_the_whole_import(tmp_path):
    """Skipping it would import 199 of 200 people and report a denominator
    nobody could tell was short."""
    from server import roster
    path = tmp_path / "typo.csv"
    path.write_text("email\na@x.com\nnot-an-address\n", encoding="utf-8")
    with pytest.raises(ValueError) as problem:
        roster.read_csv(path)
    assert "line 3" in str(problem.value)
    assert "Nothing was imported" in str(problem.value)


def test_blank_lines_are_not_people(tmp_path):
    from server import roster
    path = tmp_path / "trailing.csv"
    path.write_text("email,name\na@x.com,A\n\n,\n", encoding="utf-8")
    assert len(roster.read_csv(path)) == 1


# ── the report, which is where any of this reaches a reader ──────────────────

def test_the_chase_list_contains_the_people_who_never_showed_up(clean):
    """The list an administrator works down was built from `learner`, so the
    people who ignored the training were the only ones not on it."""
    from server import reporting, roster
    module = _module_id()
    _learner("here@x.com", name="Here Person")
    roster.load(module, _rows("here@x.com", "absent@x.com"))

    rows = reporting.people(module)
    by_email = {r["email"]: r for r in rows}
    assert set(by_email) == {"here@x.com", "absent@x.com"}

    absent = by_email["absent@x.com"]
    assert absent["signed_in"] is False
    assert absent["id"] is None
    # Null, not zero. Nothing was measured about this person, and a 0 in a
    # score column is a measurement.
    assert absent["latest_score"] is None
    assert absent["attempts"] == 0
    assert by_email["here@x.com"]["signed_in"] is True


def test_the_chase_list_marks_somebody_with_results_and_no_roster_row(clean):
    from server import db, reporting, roster
    module = _module_id()
    learner = _learner("stranger@x.com")
    db.execute("INSERT INTO enrolment (learner_id, module_id, started_at) "
               "VALUES (%s,%s, now())", (learner["id"], module))
    roster.load(module, _rows("expected@x.com"))

    rows = {r["email"]: r for r in reporting.people(module)}
    assert rows["stranger@x.com"]["on_roster"] is False
    assert rows["expected@x.com"]["on_roster"] is True


def test_without_a_roster_the_chase_list_is_what_it_always_was(clean):
    """No roster is a state, and it must not invent one. The screen says which
    denominator it used instead."""
    from server import reporting
    module = _module_id()
    _learner("only@x.com")
    rows = reporting.people(module)
    assert [r["email"] for r in rows] == ["only@x.com"]
    assert reporting.summary(module)["roster"] is None


def test_the_summary_carries_the_roster_figures_when_there_is_one(clean):
    from server import reporting, roster
    module = _module_id()
    _learner("here@x.com")
    roster.load(module, _rows("here@x.com", "absent@x.com"))

    figures = reporting.summary(module)
    assert figures["roster"]["expected"] == 2
    assert figures["roster"]["never_signed_in"] == 1
    # The sign-in figures survive alongside, because "signed in and never
    # opened it" is a different failure with a different remedy.
    assert figures["people"] == 1
    assert figures["never_opened"] == 1


def test_a_person_on_the_roster_is_not_counted_twice(clean):
    """The matched half and the roster half of the list are the same person."""
    from server import reporting, roster
    module = _module_id()
    _learner("both@x.com")
    roster.load(module, _rows("both@x.com"))
    rows = reporting.people(module)
    assert len(rows) == 1, "one person appeared as two"
