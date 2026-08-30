"""Mint a session from the shell, for local development.

This exists so that there is no login endpoint that takes an email address and
believes it. Such an endpoint is always added "just for development" and is
always still there later, and it is a complete authentication bypass reachable
by anyone who can reach the site.

This needs shell access to the machine and the value of SESSION_SECRET, so it
grants nothing that its user does not already have.

    python -m server.devsession --email you@example.com --name "Your Name"
"""
from __future__ import annotations

import argparse
import uuid

from server import auth, certificate, db
from server.config import settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="", help="display name")
    parser.add_argument("--first", default="",
                        help="given name, as Entra would report it; the "
                             "certificate is printed from this and --last")
    parser.add_argument("--last", default="")
    parser.add_argument("--department", default="")
    parser.add_argument("--link", metavar="BASE_URL", nargs="?",
                        const="http://localhost:8080", default="",
                        help="print a URL that signs this person in; "
                             "needs ALLOW_DEV_SIGNIN=1 on the server")
    parser.add_argument("--oid", default="",
                        help="Entra object id to impersonate; a stable fake "
                             "is derived from the email when omitted")
    args = parser.parse_args(argv)

    settings.validate()
    db.init_schema()
    # Derived from the email so re-running the command returns the SAME person
    # rather than quietly creating a second training record each time.
    oid = args.oid or str(uuid.uuid5(uuid.NAMESPACE_URL, "dev:" + args.email))
    # Split the display name only as a fallback, and say so: guessing a
    # family name out of a display name is precisely what the real sign-in
    # refuses to do, because "De, Krishnendu (Security)" is a display name too.
    first, last = args.first, args.last
    if not first and not last and " " in args.name:
        first, _, last = args.name.rpartition(" ")
    learner = auth.upsert_learner(
        entra_oid=oid, email=args.email, upn=args.email,
        display_name=args.name or args.email.split("@")[0],
        department=args.department, given_name=first, family_name=last)
    token = auth.issue(oid)

    print("learner #%s  %s  (certificate: %r)"
          % (learner["id"], learner["email"],
             certificate.printed_name(learner["given_name"],
                                      learner["family_name"],
                                      learner["display_name"],
                                      learner["email"])))
    print()
    if args.link:
        print("Open this in a browser to sign in:")
        print()
        print("  %s/auth/dev?token=%s" % (args.link.rstrip("/"), token))
        print()
        print("The token is signed with SESSION_SECRET, so the link grants "
              "nothing")
        print("that its holder could not already do by setting the cookie.")
        return 0
    print("%s=%s" % (auth.COOKIE_NAME, token))
    print()
    print("  curl -s --cookie '%s=%s' http://localhost:8000/api/me"
          % (auth.COOKIE_NAME, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
