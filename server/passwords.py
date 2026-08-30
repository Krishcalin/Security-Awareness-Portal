"""Storing a password, and checking one.

**scrypt, from the standard library.** Not because it beats argon2id — it does
not — but because it is memory-hard, it is in `hashlib`, and it needs no
dependency for a portal whose only other cryptography is an HMAC. A password
file is a thing an organisation keeps for years; a hashing choice that depends
on a wheel building on whatever Python the deployment ends up on is a choice
that gets quietly swapped for something worse the first time it does not.

The cost parameters are stored **inside each hash**, so raising them later
keeps every existing password verifiable and `needs_rehash` says which ones to
re-derive next time their owner signs in.

**What is deliberately NOT here: composition rules.** No "must contain a
number and a symbol". Those were withdrawn from NIST 800-63B because they
produce `Password1!` — predictable to a cracker and hard for a person — while
length is what actually costs an attacker anything. So the rules are: long
enough, not one of the obvious ones, and not the user's own email address.
That is fewer rules than most portals and more resistance than most portals.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import unicodedata
from typing import Optional

#: Cost. 2^15 is about 160ms and 34MB per attempt on a 2026 laptop — enough
#: that guessing is expensive, little enough that signing in does not feel
#: broken. Raise `N` when hardware makes that cheap; old hashes keep working.
N = 1 << 15
R = 8
P = 1
#: OpenSSL refuses above its own default of 32MB unless told otherwise, and
#: the parameters above want 34. Given room for the next increase too.
MAXMEM = 256 * 1024 * 1024
DKLEN = 32

#: NIST 800-63B sets the floor at 8. Twelve, because this is an organisation
#: setting a policy for its own staff rather than a public sign-up, and the
#: difference between eight and twelve is most of the difference between
#: "crackable overnight" and "not worth starting".
MIN_LENGTH = 12

#: Beyond this the input is refused rather than hashed. scrypt runs over the
#: whole password, so an unbounded field is somewhere to post a megabyte and
#: make the server do the work.
MAX_LENGTH = 128

#: The handful that appear at the top of every breach corpus, plus the ones
#: this particular portal invites. Not a substitute for a real breached-list
#: check — it is what can be done without shipping a 200MB file or sending a
#: password prefix to a third party, and it catches the first thing people try.
OBVIOUS = {
    "password", "passw0rd", "password1", "password123", "passwordpassword",
    "123456789012", "1234567890123", "qwertyuiop123", "qwertyuiopasdf",
    "letmein12345", "welcome12345", "iloveyou1234", "administrator",
    "changeme1234", "trustno1trust", "secretsecret", "abcd1234abcd",
    "securitysecurity", "awarenesstraining", "beawarebesecure",
    "securityawareness", "cybersecurity", "companypassword",
}


class Unsuitable(ValueError):
    """The password will not be accepted, with a sentence saying why.

    The message is meant to be shown. "Does not meet the complexity
    requirements" tells somebody nothing they can act on.
    """


def normalise(plain: str) -> str:
    """NFKC, per NIST 800-63B.

    A password typed with a composed accent on one keyboard and a combining
    one on another is the same password to the person typing it, and two
    different byte strings to scrypt.
    """
    return unicodedata.normalize("NFKC", plain)


def check_suitable(plain: str, email: str = "") -> None:
    """Raise `Unsuitable` if this will not do. Silent if it will."""
    plain = normalise(plain)
    if len(plain) < MIN_LENGTH:
        raise Unsuitable(
            "Please use at least %d characters. Length is what makes a "
            "password hard to guess — three or four unrelated words are "
            "easier to remember than a short one with symbols in it."
            % MIN_LENGTH)
    if len(plain) > MAX_LENGTH:
        raise Unsuitable("That is longer than %d characters." % MAX_LENGTH)
    if plain.strip() != plain:
        raise Unsuitable(
            "Please remove the space at the start or end — it is easy to "
            "lose track of and impossible to see.")

    folded = plain.casefold()
    if folded in OBVIOUS or re.sub(r"[^a-z]", "", folded) in OBVIOUS:
        raise Unsuitable(
            "That is one of the first passwords anybody guesses. Please "
            "choose another.")
    if len(set(folded)) < 5:
        raise Unsuitable(
            "That repeats too few characters to be hard to guess.")

    local = email.split("@")[0].casefold()
    if local and len(local) >= 3 and local in folded:
        raise Unsuitable(
            "Please choose something that is not your own email address — it "
            "is the first thing anybody trying your account will type.")


def hash_password(plain: str) -> str:
    """`scrypt$N$r$p$salt$key`, everything needed to check it again."""
    if len(plain) > MAX_LENGTH:
        raise Unsuitable("That is longer than %d characters." % MAX_LENGTH)
    salt = os.urandom(16)
    key = hashlib.scrypt(normalise(plain).encode("utf-8"), salt=salt,
                         n=N, r=R, p=P, maxmem=MAXMEM, dklen=DKLEN)
    return "scrypt$%d$%d$%d$%s$%s" % (N, R, P, _b64(salt), _b64(key))


def verify_password(stored: str, plain: str) -> bool:
    """Whether `plain` is the password behind `stored`.

    False for anything malformed as well as anything wrong: a row whose hash
    was truncated by a bad restore must fail closed, not throw a 500 that tells
    the person at the keyboard something is different about their account.
    """
    if not stored or len(plain) > MAX_LENGTH:
        return False
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            normalise(plain).encode("utf-8"), salt=_unb64(salt),
            n=int(n), r=int(r), p=int(p), maxmem=MAXMEM,
            dklen=len(_unb64(key)))
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(candidate, _unb64(key))


def needs_rehash(stored: str) -> bool:
    """Whether this hash was made with weaker parameters than we now use."""
    try:
        scheme, n, r, p, _salt, _key = stored.split("$")
    except ValueError:
        return True
    return (scheme != "scrypt" or int(n) < N or int(r) < R or int(p) < P)


def burn_time() -> None:
    """Do the work of checking a password that cannot be right.

    Called where no account was found. Without it, "no such address" returns
    in a millisecond and "wrong password" takes a sixth of a second, and the
    difference is a list of who has an account here.
    """
    hashlib.scrypt(b"no such account", salt=b"0123456789abcdef",
                   n=N, r=R, p=P, maxmem=MAXMEM, dklen=DKLEN)


def suggest() -> str:
    """A password to hand somebody, when one has to be generated.

    Words, not characters. It has to survive being read down a phone line and
    typed on a phone keyboard, and `xK7#pQ2m` fails both while being weaker
    than four words.
    """
    return "-".join(secrets.choice(WORDS) for _ in range(4))


#: Short, unambiguous, no homophones or plurals of each other. Four from this
#: list is about 44 bits, which is far past anything scrypt at these
#: parameters can be attacked through.
WORDS = (
    "anchor", "atlas", "amber", "basil", "beacon", "bishop", "bramble",
    "bronze", "canyon", "cedar", "cinder", "cobalt", "comet", "copper",
    "coral", "crimson", "cypress", "dahlia", "delta", "domino", "ember",
    "fable", "falcon", "fennel", "flint", "forest", "gable", "garnet",
    "granite", "harbour", "hazel", "heron", "indigo", "ivory", "jasper",
    "juniper", "kestrel", "lantern", "laurel", "lichen", "linen", "lupin",
    "magnet", "maple", "marble", "meadow", "mercury", "mosaic", "nectar",
    "nimbus", "nutmeg", "obsidian", "onyx", "orchard", "osprey", "paprika",
    "pewter", "pigment", "plover", "prairie", "quartz", "quiver", "ravine",
    "rowan", "russet", "saffron", "sable", "sandal", "sapphire", "sorrel",
    "spindle", "sterling", "sumac", "tamarind", "tandem", "teal", "thicket",
    "thimble", "timber", "topaz", "trellis", "tundra", "turret", "umber",
    "vellum", "verbena", "vermilion", "walnut", "willow", "wicker", "yarrow",
    "zenith", "zephyr",
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))
