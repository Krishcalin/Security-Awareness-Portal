"""Training that comes round again.

The thing being protected is a year boundary. Everything about a cycle is easy
to get subtly wrong in a way nobody notices until the following March: a figure
that quietly spans four years, a course that reopens but resumes at slide
thirty-one, a certificate that gets overwritten by the retake that replaced it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import needs_db

from server import auth, cycles, db

SLUG = "security-awareness-essentials"


@pytest.fixture
def person(clean):
    oid = str(uuid.uuid4())
    auth.upsert_learner(entra_oid=oid, email="j.rao@example.com",
                        given_name="Jaya", family_name="Rao")
    clean.cookies.set(auth.COOKIE_NAME, auth.issue(oid))
    return clean


def _module_id() -> int:
    return db.one("SELECT id FROM module WHERE slug = %s", (SLUG,))["id"]


def _cycles_gone():
    db.execute("DELETE FROM training_cycle WHERE module_id = %s", (_module_id(),))


@pytest.fixture(autouse=True)
def no_cycles_left_behind(clean):
    """`clean` truncates the people tables; cycles hang off the module, which
    it deliberately does not touch.

    The people go first on the way out. A cycle cannot be deleted while an
    attempt or a certificate still points at it — deliberately, since a period
    somebody was certified in is not a thing to erase — so a teardown that
    tried the other order would be testing that constraint rather than
    cleaning up.
    """
    _cycles_gone()
    yield
    db.execute("TRUNCATE response, attempt, enrolment, learner "
               "RESTART IDENTITY CASCADE")
    _cycles_gone()


def sit(client, correct: int):
    started = client.post("/api/modules/%s/attempts" % SLUG).json()
    keys = {row["ordinal"]: row["correct_index"] for row in db.query(
        "SELECT q.ordinal, q.correct_index FROM question q JOIN module m "
        "ON m.id = q.module_id WHERE m.slug = %s", (SLUG,))}
    for i, question in enumerate(started["questions"]):
        key = keys[question["ordinal"]]
        chosen = key if i < correct else (key + 1) % len(question["options"])
        client.post("/api/attempts/%d/responses" % started["attempt_id"],
                    json={"ordinal": question["ordinal"],
                          "chosen_index": chosen, "took_ms": 5000})
    return client.post("/api/attempts/%d/finish"
                       % started["attempt_id"]).json()


# --------------------------------------------------------------------------
# A portal nobody has set a cycle on behaves as it always did
# --------------------------------------------------------------------------

@needs_db
def test_with_no_cycles_a_pass_still_closes_the_course(person):
    """The feature is something an administrator turns on, not something that
    changes underneath a portal already in use."""
    sit(person, 10)
    detail = person.get("/api/modules/%s" % SLUG).json()
    assert detail["lessons"] == []
    assert detail["completed"]["serial"]
    assert detail["cycle"] is None
    assert person.post("/api/modules/%s/attempts" % SLUG).status_code == 409


# --------------------------------------------------------------------------
# Opening one
# --------------------------------------------------------------------------

@needs_db
def test_opening_a_cycle_reopens_the_course_for_somebody_who_passed(person):
    earned = sit(person, 10)
    assert person.get("/api/modules/%s" % SLUG).json()["lessons"] == []

    cycles.open_cycle(_module_id(), "2027 annual refresher")

    detail = person.get("/api/modules/%s" % SLUG).json()
    assert len(detail["lessons"]) > 1
    assert detail["completed"] is None
    assert detail["cycle"]["name"] == "2027 annual refresher"
    assert person.post("/api/modules/%s/attempts" % SLUG).status_code == 200

    # And they are told why the course is in front of them again, with the
    # certificate they already hold.
    assert detail["previously"]["serial"] == earned["certificate"]["serial"]


@needs_db
def test_last_years_certificate_is_untouched(person):
    """Nothing is invalidated. Last year's is still the record of last year,
    which is what an auditor is asking about when they ask about last year."""
    earned = sit(person, 10)
    cycles.open_cycle(_module_id(), "2027 annual refresher")

    serial = earned["certificate"]["serial"]
    got = person.get("/api/certificates/%s" % serial)
    assert got.status_code == 200
    assert got.content.startswith(b"%PDF")
    assert db.one("SELECT count(*) AS n FROM certificate")["n"] == 1


@needs_db
def test_the_new_cycle_starts_at_the_beginning(person):
    """Reopening a course and then resuming it at slide thirty-one would be
    worse than not reopening it."""
    person.post("/api/modules/%s/progress" % SLUG,
                json={"furthest_ordinal": 31})
    sit(person, 10)
    cycles.open_cycle(_module_id(), "2027 annual refresher")

    listed = [m for m in person.get("/api/modules").json()
              if m["slug"] == SLUG][0]
    assert listed["furthest_ordinal"] == 0
    assert listed["passed"] is False
    # Nothing to resume: they have not started this one. The front page is
    # where the course is offered, and it now offers it.
    assert person.get("/api/resume").json()["path"] == "/"

    # But an unfinished row from LAST cycle must not be mistaken for this
    # cycle's progress either.
    person.post("/api/modules/%s/progress" % SLUG, json={"furthest_ordinal": 3})
    assert person.get("/api/resume").json()["path"] ==         "/module/%s?slide=3" % SLUG


@needs_db
def test_last_cycles_progress_is_still_on_record(person):
    """A new row rather than an overwritten one: how far somebody got when
    they did not finish is a fact about that year."""
    person.post("/api/modules/%s/progress" % SLUG,
                json={"furthest_ordinal": 18})
    sit(person, 10)
    cycles.open_cycle(_module_id(), "2027 annual refresher")
    person.post("/api/modules/%s/progress" % SLUG, json={"furthest_ordinal": 2})

    rows = db.query(
        "SELECT cycle_id, furthest_ordinal FROM enrolment "
        "WHERE module_id = %s ORDER BY furthest_ordinal", (_module_id(),))
    assert [r["furthest_ordinal"] for r in rows] == [2, 18]
    assert rows[1]["cycle_id"] is None          # the year before cycles existed


@needs_db
def test_passing_again_earns_a_certificate_stamped_with_this_cycle(person):
    first = sit(person, 10)
    cycle = cycles.open_cycle(_module_id(), "2027 annual refresher")
    second = sit(person, 9)

    assert second["certificate"]["serial"] != first["certificate"]["serial"]
    stamped = db.one("SELECT cycle_id FROM certificate WHERE serial = %s",
                     (second["certificate"]["serial"],))
    assert stamped["cycle_id"] == cycle["id"]
    # And the course closes again, for this cycle.
    assert person.get("/api/modules/%s" % SLUG).json()["lessons"] == []


@needs_db
def test_the_attempt_number_starts_again(person):
    """"Passed on the third go" has to mean this year's third go. Counted
    against this cycle's enrolment, not against every attempt ever made."""
    sit(person, 4)
    sit(person, 10)
    cycles.open_cycle(_module_id(), "2027 annual refresher")
    sit(person, 10)

    from server import reporting
    row = [r for r in reporting.people(_module_id())
           if r["email"] == "j.rao@example.com"][0]
    assert row["attempts"] == 1

    # And the NUMBER restarts, not just the count. "Passed first time" is read
    # off attempt_no, so a number that kept climbing would report somebody who
    # passed this year's check on their first go as a third-attempt pass —
    # which is the one figure on that screen carrying the most evidence.
    numbers = db.query(
        "SELECT cycle_id, attempt_no FROM attempt "
        "WHERE module_id = %s ORDER BY id", (_module_id(),))
    assert [a["attempt_no"] for a in numbers] == [1, 2, 1]
    assert numbers[2]["cycle_id"] == cycles.current(_module_id())["id"]
    assert reporting.summary(_module_id())["passed_first_time"] == 1


# --------------------------------------------------------------------------
# When a cycle takes effect
# --------------------------------------------------------------------------

@needs_db
def test_a_cycle_dated_ahead_is_not_in_force_yet(person):
    """Set up in advance is the normal way to do it, and it must not reopen
    the course the moment somebody types the command."""
    sit(person, 10)
    later = datetime.now(timezone.utc) + timedelta(days=30)
    cycles.open_cycle(_module_id(), "2027 annual refresher", opens_at=later)

    assert cycles.current(_module_id()) is None
    assert person.get("/api/modules/%s" % SLUG).json()["lessons"] == []


@needs_db
def test_a_pass_inside_the_new_cycle_is_adopted_by_it(person):
    """Opening a cycle backdated to the start of the year must not ask the
    people who have already done this year's training to do it twice."""
    sit(person, 10)
    started = datetime.now(timezone.utc) - timedelta(days=7)
    cycle = cycles.open_cycle(_module_id(), "2027 annual refresher",
                              opens_at=started)
    assert cycle["adopted"] == 1

    detail = person.get("/api/modules/%s" % SLUG).json()
    assert detail["lessons"] == []
    assert detail["completed"]["serial"]


@needs_db
def test_the_latest_opened_cycle_is_the_one_in_force(clean):
    """And a cycle dated ahead of today is not it. Relative to now rather
    than to fixed years, or this test stops meaning anything in January."""
    module_id = _module_id()
    now = datetime.now(timezone.utc)
    cycles.open_cycle(module_id, "two years ago",
                      opens_at=now - timedelta(days=730))
    cycles.open_cycle(module_id, "last year", opens_at=now - timedelta(days=365))
    cycles.open_cycle(module_id, "next year", opens_at=now + timedelta(days=365))
    assert cycles.current(module_id)["name"] == "last year"


# --------------------------------------------------------------------------
# What the administrator sees
# --------------------------------------------------------------------------

@needs_db
def test_the_report_counts_this_cycle_only(person):
    """A figure that quietly spans every period there has ever been answers a
    question nobody asked."""
    from server import reporting
    sit(person, 10)
    assert reporting.summary(_module_id())["passed"] == 1

    cycles.open_cycle(_module_id(), "2027 annual refresher")
    figures = reporting.summary(_module_id())
    assert figures["passed"] == 0
    assert figures["cycle"]["name"] == "2027 annual refresher"
    assert figures["people"] == 1

    row = [r for r in reporting.people(_module_id())
           if r["email"] == "j.rao@example.com"][0]
    assert row["certificate"] is None       # not for THIS cycle
    assert row["completed_at"] is None


@needs_db
def test_an_overdue_cycle_says_so(clean):
    from server import reporting
    cycles.open_cycle(
        _module_id(), "2026 refresher",
        opens_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        due_at=datetime(2026, 3, 31, tzinfo=timezone.utc))
    assert reporting.summary(_module_id())["overdue"] is True


@needs_db
def test_a_cycle_with_no_due_date_is_never_overdue(clean):
    from server import reporting
    cycles.open_cycle(_module_id(), "rolling")
    assert reporting.summary(_module_id())["overdue"] is False


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------

@needs_db
def test_the_command_lists_nothing_and_explains_why(clean, capsys):
    from server import cycle
    assert cycle.main(["--list"]) == 0
    said = capsys.readouterr().out
    assert "no training cycles" in said
    assert "as it always has" in said


@needs_db
def test_the_command_opens_one_and_says_who_is_now_due(person, capsys):
    from server import cycle
    sit(person, 10)
    capsys.readouterr()

    assert cycle.main(["--open", "2027 annual refresher",
                       "--due", "2027-03-31"]) == 0
    said = capsys.readouterr().out
    assert "1 people who had already passed are now due again" in said
    assert "certificates are untouched" in said
    assert cycles.current(_module_id())["name"] == "2027 annual refresher"


@needs_db
def test_a_due_date_means_the_end_of_that_day(clean):
    """"Due by the 31st" is not "due at midnight as the 31st begins"."""
    from server import cycle
    cycle.main(["--open", "2027", "--due", "2027-03-31"])
    due = cycles.current(_module_id())["due_at"]
    assert (due.year, due.month, due.day, due.hour) == (2027, 3, 31, 23)


@needs_db
def test_the_command_refuses_a_name_already_used(clean):
    from server import cycle
    cycle.main(["--open", "2027"])
    with pytest.raises(SystemExit) as refused:
        cycle.main(["--open", "2027"])
    assert "already has a cycle" in str(refused.value)


@needs_db
def test_the_command_refuses_a_due_date_before_it_opens(clean):
    from server import cycle
    with pytest.raises(SystemExit) as refused:
        cycle.main(["--open", "2027", "--opens", "2027-06-01",
                    "--due", "2027-03-31"])
    assert "before the cycle opens" in str(refused.value)


@needs_db
def test_the_command_refuses_a_date_it_cannot_read(clean):
    from server import cycle
    with pytest.raises(SystemExit) as refused:
        cycle.main(["--open", "2027", "--due", "31/03/2027"])
    assert "2027-03-31" in str(refused.value)
