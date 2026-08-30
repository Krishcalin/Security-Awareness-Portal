"""Create and manage the accounts that sign in with a password.

    python -m server.account --create --email j.rao@contractor.example \\
                             --first Jaya --last Rao --department "Unit 3 O&M"
    python -m server.account --reset  --email j.rao@contractor.example
    python -m server.account --unlock --email j.rao@contractor.example
    python -m server.account --disable --email j.rao@contractor.example
    python -m server.account --list

From the shell, like `server.grant`, and for the same reason: there is no
self-service sign-up. This is an internal compliance portal, and an account
here is a claim that a named person exists and is required to do the training.
An endpoint that lets anybody create one is an endpoint that lets anybody
manufacture a completion record with a plausible name on it.

**The password is printed once and never stored in a form anyone can read
back.** It is generated rather than chosen on the command line, because a
password typed as an argument is in the shell history, in `ps`, and in
whatever collects the terminal's scrollback. `--reset` prints a new one the
same way.

**Whoever runs this cannot afterwards sign in as that person.** The account is
created needing a password change, so the generated one is good for exactly
one sign-in. The certificate names a person; if the administrator still knew
the password it would name a person or an administrator, and the distinction
is the whole value of it.
"""
from __future__ import annotations

import argparse
import re
import sys

from server import auth, db, passwords
from server.config import settings

#: Enough to catch a typo, not enough to argue with a real address. Anything
#: stricter rejects somebody's actual mailbox sooner or later.
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _find(email: str):
    return db.one(
        "SELECT id, email, display_name, entra_oid, role, "
        "       password_hash <> '' AS has_password, password_must_change, "
        "       locked_until, failed_signins "
        "FROM learner WHERE lower(email) = lower(%s)", (email.strip(),))


def create(args) -> int:
    if not ADDRESS.match(args.email or ""):
        raise SystemExit("--email needs to look like an email address.")
    if not (args.first and args.last):
        raise SystemExit(
            "--first and --last are required.\n"
            "  They are printed on the certificate, and a certificate with a\n"
            "  blank where the name goes is worse than no certificate.")

    existing = _find(args.email)
    if existing:
        if existing["has_password"]:
            raise SystemExit(
                "%s already has a password account. Use --reset to issue a "
                "new password." % existing["email"])
        if existing["entra_oid"]:
            raise SystemExit(
                "%s already signs in with Microsoft, and has a training\n"
                "  record under that identity. Adding a password to it would\n"
                "  give the same person two ways in and one record, which is\n"
                "  fine — but it is not something to do by accident, so it is\n"
                "  not what --create does. Use --add-password if that is\n"
                "  really what you want." % existing["email"])

    plain = passwords.suggest()
    display = "%s %s" % (args.first.strip(), args.last.strip())

    if existing:
        db.execute(
            "UPDATE learner SET given_name = %s, family_name = %s, "
            "       display_name = %s, department = %s WHERE id = %s",
            (args.first.strip(), args.last.strip(), display,
             args.department or "", existing["id"]))
        learner_id = existing["id"]
    else:
        learner_id = db.one(
            "INSERT INTO learner (email, given_name, family_name, "
            "                     display_name, department) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (args.email.strip(), args.first.strip(), args.last.strip(),
             display, args.department or ""))["id"]

    auth.set_password(learner_id, plain, must_change=True)
    _announce(args.email.strip(), plain, created=not existing)
    return 0


def add_password(args) -> int:
    """Give an existing directory account a password as well.

    Separate from --create so that attaching a second way into somebody's
    training record is always something somebody typed on purpose.
    """
    learner = _find(args.email)
    if not learner:
        raise SystemExit("no account with that address.")
    plain = passwords.suggest()
    auth.set_password(learner["id"], plain, must_change=True)
    _announce(learner["email"], plain, created=False)
    return 0


def reset(args) -> int:
    learner = _find(args.email)
    if not learner:
        raise SystemExit("no account with that address.")
    plain = passwords.suggest()
    auth.set_password(learner["id"], plain, must_change=True)
    print("Password reset for %s." % learner["email"])
    _announce(learner["email"], plain, created=False, heading=False)
    return 0


def unlock(args) -> int:
    learner = _find(args.email)
    if not learner:
        raise SystemExit("no account with that address.")
    if not learner["locked_until"]:
        print("%s is not locked." % learner["email"])
        return 0
    db.execute("UPDATE learner SET failed_signins = 0, locked_until = NULL "
               "WHERE id = %s", (learner["id"],))
    print("%s unlocked after %d failed attempts."
          % (learner["email"], learner["failed_signins"]))
    print()
    print("Worth asking whether they were all this person. Ten wrong")
    print("passwords in a row is either a forgotten one or somebody else.")
    return 0


def disable(args) -> int:
    """Take away the password without touching the training record.

    Not a delete. The record of what somebody completed is the thing this
    portal exists to keep, and it outlives their account here.
    """
    learner = _find(args.email)
    if not learner:
        raise SystemExit("no account with that address.")
    if not learner["has_password"]:
        print("%s has no password to remove." % learner["email"])
        return 0
    db.execute("UPDATE learner SET password_hash = '', "
               "password_must_change = false WHERE id = %s", (learner["id"],))
    # And end whatever they are signed into. Removing the password while
    # leaving the browser session alive answers the wrong half of the problem.
    auth.revoke_sessions(learner["id"])
    print("Password removed for %s, and every session they held has ended. "
          "Their training record is untouched." % learner["email"])
    if learner["entra_oid"]:
        print("They can still sign in with Microsoft.")
    return 0


def listing(_args) -> int:
    rows = db.query(
        "SELECT email, display_name, role, entra_oid IS NOT NULL AS entra, "
        "       password_hash <> '' AS has_password, password_must_change, "
        "       locked_until > now() AS locked "
        "FROM learner ORDER BY email")
    if not rows:
        print("no accounts yet.")
        return 0
    print("%-40s %-22s %-10s %s" % ("email", "name", "signs in with", "state"))
    for row in rows:
        ways = []
        if row["entra"]:
            ways.append("Microsoft")
        if row["has_password"]:
            ways.append("password")
        state = []
        if row["password_must_change"]:
            state.append("must change password")
        if row["locked"]:
            state.append("LOCKED")
        if row["role"] != "learner":
            state.append(row["role"])
        print("%-40s %-22s %-10s %s"
              % (row["email"], row["display_name"][:22],
                 " + ".join(ways) or "nothing", ", ".join(state)))
    return 0


def _announce(email: str, plain: str, created: bool,
              heading: bool = True) -> None:
    if heading:
        print("%s %s." % ("Created" if created else "Updated", email))
    print()
    print("  Password:  %s" % plain)
    print()
    print("Give it to them directly, not by email — an emailed password sits")
    print("in two mailboxes for as long as both exist. It is good for one")
    print("sign-in: the portal asks them to choose their own before it lets")
    print("them do anything, so nobody else knows it afterwards.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email")
    parser.add_argument("--first", help="given name, as it goes on the certificate")
    parser.add_argument("--last", help="family name, as it goes on the certificate")
    parser.add_argument("--department", default="")

    what = parser.add_mutually_exclusive_group()
    what.add_argument("--create", action="store_true")
    what.add_argument("--add-password", action="store_true",
                      help="give an existing Microsoft account a password too")
    what.add_argument("--reset", action="store_true",
                      help="issue a new one-time password")
    what.add_argument("--unlock", action="store_true")
    what.add_argument("--disable", action="store_true",
                      help="remove the password; keeps the training record")
    what.add_argument("--list", action="store_true")

    args = parser.parse_args(argv)
    settings.validate()

    if args.list or not any((args.create, args.add_password, args.reset,
                             args.unlock, args.disable)):
        return listing(args)
    if not args.email:
        parser.error("--email is required")

    return {"create": create, "add_password": add_password, "reset": reset,
            "unlock": unlock, "disable": disable}[
        "create" if args.create else
        "add_password" if args.add_password else
        "reset" if args.reset else
        "unlock" if args.unlock else "disable"](args)


if __name__ == "__main__":
    sys.exit(main())
