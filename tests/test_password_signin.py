"""Signing in with a password, for the people the directory does not reach.

The properties worth holding on to are the ones that are invisible when they
break: that a wrong password and an unknown address are indistinguishable,
that ten guesses stop the eleventh, that a password an administrator issued
buys exactly one page, and that a session for a local account is not a session
for a directory identity.
"""
from __future__ import annotations

import re

import pytest

from tests.conftest import needs_db

from server import passwords


# --------------------------------------------------------------------------
# Hashing, which needs no database
# --------------------------------------------------------------------------

def test_a_password_verifies_against_its_own_hash():
    stored = passwords.hash_password("marble-thicket-plover-quiver")
    assert passwords.verify_password(stored, "marble-thicket-plover-quiver")
    assert not passwords.verify_password(stored, "marble-thicket-plover-quiveR")
    assert not passwords.verify_password(stored, "")


def test_the_same_password_hashes_differently_every_time():
    """Salted. Two people who choose the same password must not be visible as
    two identical rows."""
    first = passwords.hash_password("marble-thicket-plover-quiver")
    second = passwords.hash_password("marble-thicket-plover-quiver")
    assert first != second
    assert passwords.verify_password(first, "marble-thicket-plover-quiver")
    assert passwords.verify_password(second, "marble-thicket-plover-quiver")


@pytest.mark.parametrize("damaged", [
    "", "not-a-hash", "scrypt$32768$8$1$onlyfourfields",
    "bcrypt$32768$8$1$c2FsdA==$a2V5", "scrypt$x$8$1$c2FsdA==$a2V5",
    "scrypt$32768$8$1$!!!notbase64!!!$a2V5",
])
def test_a_damaged_hash_fails_closed(damaged):
    """A row mangled by a bad restore must refuse the password, not raise.

    A 500 on one account and a clean 401 on every other one tells the person
    at the keyboard that something about theirs is different.
    """
    assert passwords.verify_password(damaged, "anything at all") is False


def test_the_password_is_normalised_before_it_is_hashed():
    """An accent composed one way on one keyboard and another way on another
    is the same password to the person typing it."""
    composed = "crème-brulee-marble-quiver"        # è as one character
    decomposed = "crème-brulee-marble-quiver"     # e + combining grave
    assert composed != decomposed
    assert passwords.verify_password(passwords.hash_password(composed),
                                     decomposed)


def _hashed_weakly(plain: str, monkeypatch) -> str:
    """A hash genuinely derived with yesterday's cost, not one with the
    numbers rewritten — those do not verify, which would make every test
    built on them pass for the wrong reason."""
    monkeypatch.setattr(passwords, "N", 1024)
    try:
        return passwords.hash_password(plain)
    finally:
        monkeypatch.undo()


def test_a_hash_made_with_weaker_parameters_is_noticed(monkeypatch):
    weak = _hashed_weakly("marble-thicket-plover-quiver", monkeypatch)
    assert passwords.verify_password(weak, "marble-thicket-plover-quiver")
    assert passwords.needs_rehash(weak)
    assert not passwords.needs_rehash(
        passwords.hash_password("marble-thicket-plover-quiver"))


@pytest.mark.parametrize("bad, because", [
    ("short", "at least"),
    ("x" * (passwords.MAX_LENGTH + 1), "longer than"),
    ("password1234", "first passwords anybody guesses"),
    ("aaaaaaaaaaaaaa", "too few characters"),
    ("  marble-thicket-plover  ", "space at the start"),
])
def test_an_unsuitable_password_says_why(bad, because):
    with pytest.raises(passwords.Unsuitable) as refused:
        passwords.check_suitable(bad, "someone@example.com")
    assert because in str(refused.value)


def test_a_password_cannot_be_the_persons_own_address():
    with pytest.raises(passwords.Unsuitable) as refused:
        passwords.check_suitable("a.contractor-2026!", "a.contractor@site.example")
    assert "your own email address" in str(refused.value)


def test_there_are_no_composition_rules():
    """Deliberate, and worth a test so that nobody helpfully adds some.

    Composition rules were withdrawn from NIST 800-63B because they produce
    `Password1!`. Four words with no digit and no symbol is a good password and
    must be accepted as one.
    """
    passwords.check_suitable("marble thicket plover quiver", "x@example.com")


def test_a_generated_password_passes_the_rules_it_will_be_checked_against():
    """The one that would be found by a person on the phone rather than by a
    test: an issued password that the change form then refuses."""
    for _ in range(20):
        passwords.check_suitable(passwords.suggest(), "someone@example.com")


# --------------------------------------------------------------------------
# Checking one, which does
# --------------------------------------------------------------------------

PASSWORD = "marble-thicket-plover-quiver"


def _make(email="a.contractor@site.example", password=PASSWORD,
          must_change=False):
    from server import auth, db
    learner_id = db.one(
        "INSERT INTO learner (email, given_name, family_name, display_name) "
        "VALUES (%s, 'Anita', 'Contractor', 'Anita Contractor') RETURNING id",
        (email,))["id"]
    auth.set_password(learner_id, password, must_change=must_change)
    return learner_id


@needs_db
def test_the_right_password_finds_the_learner(clean):
    from server import auth
    _make()
    learner = auth.password_sign_in("a.contractor@site.example", PASSWORD)
    assert learner["email"] == "a.contractor@site.example"


@needs_db
def test_the_address_is_matched_without_regard_to_case(clean):
    from server import auth
    _make()
    assert auth.password_sign_in("A.Contractor@Site.Example", PASSWORD)


@needs_db
def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(clean):
    """The whole point. Two different messages is a way to ask which addresses
    have accounts here, one request at a time."""
    from server import auth
    _make()

    with pytest.raises(auth.Refused) as wrong:
        auth.password_sign_in("a.contractor@site.example", "not-the-password")
    with pytest.raises(auth.Refused) as unknown:
        auth.password_sign_in("nobody@site.example", "not-the-password")
    assert str(wrong.value) == str(unknown.value)


@needs_db
def test_an_account_with_no_password_answers_the_same_way(clean):
    """Somebody who signs in with Microsoft has no password here. Saying so
    would confirm the address exists."""
    from server import auth, db
    db.execute("INSERT INTO learner (email, entra_oid) VALUES (%s, %s)",
               ("directory.person@example.com", "oid-1"))
    _make()

    with pytest.raises(auth.Refused) as no_password:
        auth.password_sign_in("directory.person@example.com", PASSWORD)
    with pytest.raises(auth.Refused) as wrong:
        auth.password_sign_in("a.contractor@site.example", "wrong")
    assert str(no_password.value) == str(wrong.value)


@needs_db
def test_ten_wrong_guesses_stop_the_eleventh(clean):
    from server import auth
    _make()
    for _ in range(auth.LOCKOUT_AFTER):
        with pytest.raises(auth.Refused):
            auth.password_sign_in("a.contractor@site.example", "wrong")

    # Even with the right one. A lockout that the correct password walks
    # through is not a lockout — it is a message telling an attacker which
    # guess was right.
    with pytest.raises(auth.Refused) as locked:
        auth.password_sign_in("a.contractor@site.example", PASSWORD)
    assert "locked" in str(locked.value)


@needs_db
def test_a_lock_that_has_run_out_starts_the_count_again(clean):
    """Otherwise the next single mistake locks the account straight back up,
    and somebody who has waited a quarter of an hour waits another one."""
    from datetime import timedelta

    from server import auth, db
    learner_id = _make()
    for _ in range(auth.LOCKOUT_AFTER):
        with pytest.raises(auth.Refused):
            auth.password_sign_in("a.contractor@site.example", "wrong")

    db.execute("UPDATE learner SET locked_until = now() - interval '1 minute' "
               "WHERE id = %s", (learner_id,))
    # One more wrong attempt does not re-lock: the count restarted at zero.
    with pytest.raises(auth.Refused) as again:
        auth.password_sign_in("a.contractor@site.example", "wrong")
    assert "locked" not in str(again.value)
    assert auth.password_sign_in("a.contractor@site.example", PASSWORD)


@needs_db
def test_signing_in_clears_the_count(clean):
    from server import auth, db
    learner_id = _make()
    for _ in range(3):
        with pytest.raises(auth.Refused):
            auth.password_sign_in("a.contractor@site.example", "wrong")
    auth.password_sign_in("a.contractor@site.example", PASSWORD)
    assert db.one("SELECT failed_signins FROM learner WHERE id = %s",
                  (learner_id,))["failed_signins"] == 0


@needs_db
def test_a_weakly_hashed_password_is_re_derived_when_it_is_next_used(
        clean, monkeypatch):
    """Signing in is the only moment the plaintext exists to do it with."""
    from server import auth, db
    learner_id = _make()
    db.execute("UPDATE learner SET password_hash = %s WHERE id = %s",
               (_hashed_weakly(PASSWORD, monkeypatch), learner_id))

    auth.password_sign_in("a.contractor@site.example", PASSWORD)
    after = db.one("SELECT password_hash FROM learner WHERE id = %s",
                   (learner_id,))["password_hash"]
    assert not passwords.needs_rehash(after)
    assert passwords.verify_password(after, PASSWORD)


# --------------------------------------------------------------------------
# The pages
# --------------------------------------------------------------------------

def _token(html: str, name: str = "form_token") -> str:
    found = re.search(r'name="%s" value="([^"]+)"' % name, html)
    assert found, "no %s in the page" % name
    return found.group(1)


@needs_db
def test_the_sign_in_page_offers_the_form(clean):
    page = clean.get("/auth/login")
    assert page.status_code == 200
    assert 'name="email"' in page.text
    assert 'name="password"' in page.text
    assert 'action="/auth/password"' in page.text


@needs_db
def test_both_ways_in_are_offered_when_the_directory_is_configured(
        clean, monkeypatch):
    from server import entra
    monkeypatch.setattr(entra, "configured", lambda: True)
    page = clean.get("/auth/login")
    assert 'name="password"' in page.text
    assert "Sign in with Microsoft" in page.text
    # The form first, the directory second: a text field below a button means
    # everybody who uses the field tabs past the button to reach it.
    assert page.text.index('name="password"') < page.text.index(
        "Sign in with Microsoft")


@needs_db
def test_a_post_with_no_form_token_is_refused(clean):
    _make()
    answer = clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD})
    assert answer.status_code == 400
    assert "did not come from here" in answer.text
    assert clean.get("/api/me").status_code == 401


@needs_db
def test_a_token_for_the_other_form_is_refused(clean):
    """The two forms are signed for their own purpose. A token lifted from one
    page must not authorise a post to the other."""
    _make(must_change=True)
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})
    change = clean.get("/auth/password/change")

    answer = clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(change.text)})
    assert answer.status_code == 400


@needs_db
def test_a_password_somebody_else_chose_reaches_one_page_and_no_further(clean):
    """An issued password proves enough to choose your own, and nothing else.

    The certificate names a person. If whoever provisioned the account could
    still sign in as them, it names that person or an administrator, and the
    difference is the whole evidentiary value of it.
    """
    _make(must_change=True)
    page = clean.get("/auth/login")
    answer = clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)}, follow_redirects=False)

    assert answer.status_code == 303
    assert answer.headers["location"] == "/auth/password/change"
    assert clean.get("/api/me").status_code == 401     # no session yet
    assert "Choose your password" in clean.get("/auth/password/change").text


@needs_db
def test_choosing_a_password_signs_them_in(clean):
    _make(must_change=True)
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})

    change = clean.get("/auth/password/change")
    answer = clean.post("/auth/password/change", data={
        "current": PASSWORD, "fresh": "garnet-lantern-osprey-tundra",
        "again": "garnet-lantern-osprey-tundra",
        "form_token": _token(change.text)}, follow_redirects=False)

    assert answer.status_code == 303
    me = clean.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "a.contractor@site.example"


@needs_db
def test_the_change_form_refuses_a_wrong_current_password(clean):
    _make(must_change=True)
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})

    change = clean.get("/auth/password/change")
    answer = clean.post("/auth/password/change", data={
        "current": "not-the-password", "fresh": "garnet-lantern-osprey-tundra",
        "again": "garnet-lantern-osprey-tundra",
        "form_token": _token(change.text)})
    assert answer.status_code == 401
    assert clean.get("/api/me").status_code == 401


@needs_db
@pytest.mark.parametrize("fresh, again, says", [
    ("garnet-lantern-osprey-tundra", "garnet-lantern-osprey-tundrb",
     "not the same"),
    ("short", "short", "at least"),
    (PASSWORD, PASSWORD, "already have"),
])
def test_the_change_form_refuses_what_it_should(clean, fresh, again, says):
    _make(must_change=True)
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})

    change = clean.get("/auth/password/change")
    answer = clean.post("/auth/password/change", data={
        "current": PASSWORD, "fresh": fresh, "again": again,
        "form_token": _token(change.text)})
    assert answer.status_code == 400
    assert says in answer.text
    assert clean.get("/api/me").status_code == 401


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

@needs_db
def test_a_local_session_is_not_a_directory_session(clean):
    """`oid` and `lid` are looked up separately and never interchangeably.

    Otherwise creating an address in the tenant would be a way to reach the
    record of somebody who signs in with a password — including a certificate
    already issued in their name.
    """
    from server import auth
    learner_id = _make()

    # A cookie naming this learner the way a directory session would.
    clean.cookies.clear()
    clean.cookies.set(auth.COOKIE_NAME, auth.sign({"oid": str(learner_id)}))
    assert clean.get("/api/me").status_code == 401


@needs_db
def test_removing_the_password_ends_the_session_it_opened(clean):
    """A signed cookie cannot be taken back on its own, which is what the
    epoch is for. Without it "this account is closed" means "in ten hours"."""
    from server import auth
    learner_id = _make()
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})
    assert clean.get("/api/me").status_code == 200

    auth.revoke_sessions(learner_id)
    assert clean.get("/api/me").status_code == 401


@needs_db
def test_resetting_a_password_ends_the_sessions_the_old_one_opened(clean):
    """Somebody resetting a password usually believes the old one is known to
    somebody else."""
    from server import auth
    learner_id = _make()
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})
    assert clean.get("/api/me").status_code == 200

    auth.set_password(learner_id, "garnet-lantern-osprey-tundra",
                      must_change=True)
    assert clean.get("/api/me").status_code == 401


@needs_db
def test_signing_out_of_a_password_session_does_not_bounce_to_microsoft(
        clean, monkeypatch):
    """Sending somebody who signed in with a password to a Microsoft sign-out
    page ends nothing, and it is an unexpected bounce to a login-looking
    screen on another domain — the shape this course teaches people to
    distrust."""
    from server import entra
    monkeypatch.setattr(entra, "configured", lambda: True)
    _make()
    page = clean.get("/auth/login")
    clean.post("/auth/password", data={
        "email": "a.contractor@site.example", "password": PASSWORD,
        "form_token": _token(page.text)})

    out = clean.get("/auth/logout", follow_redirects=False)
    assert out.headers["location"] == "/"
    assert clean.get("/api/me").status_code == 401
