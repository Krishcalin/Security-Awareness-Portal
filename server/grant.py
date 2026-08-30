"""Grant or withdraw the role that can see everybody's results.

    python -m server.grant --email ciso@example.com --role admin
    python -m server.grant --email ciso@example.com --role learner
    python -m server.grant --list

From the shell, deliberately. The application itself never sets this: a
privilege the application can grant is one bug away from a learner granting it
to themselves. Where the tenant is configured for it, ENTRA_ADMIN_GROUP does
the same job from the directory, which is better because it is withdrawn when
somebody changes job.

The person must have signed in at least once, so that this grants a role to an
identity the directory has already vouched for rather than creating an account
out of an email address somebody typed.
"""
from __future__ import annotations

import argparse

from server import db
from server.config import settings

ROLES = ("learner", "admin")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email")
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--list", action="store_true",
                        help="show who currently holds a role")
    args = parser.parse_args(argv)

    settings.validate()

    if args.list or not args.email:
        holders = db.query(
            "SELECT email, display_name, role FROM learner "
            "WHERE role <> 'learner' ORDER BY email")
        if not holders:
            print("nobody has a role beyond learner.")
        for row in holders:
            print("  %-40s %-24s %s"
                  % (row["email"], row["display_name"], row["role"]))
        if not args.email:
            return 0

    if not args.role:
        parser.error("--role is required with --email")

    learner = db.one("SELECT id, email, role FROM learner WHERE email = %s",
                     (args.email,))
    if not learner:
        raise SystemExit(
            "no learner with that address has signed in yet.\n"
            "  A role is granted to an identity the directory has already\n"
            "  vouched for, not to an address typed on a command line.")

    if learner["role"] == args.role:
        print("%s is already %s." % (learner["email"], args.role))
        return 0

    db.execute("UPDATE learner SET role = %s WHERE id = %s",
               (args.role, learner["id"]))
    print("%s: %s -> %s" % (learner["email"], learner["role"], args.role))
    if args.role == "admin":
        print()
        print("They can now see every learner's completion and results.")
        print("That is individual data, not anonymous statistics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
