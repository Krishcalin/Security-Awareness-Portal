"""The README's figures, checked against the code.

Every number in a README is a claim, and claims go stale silently — nobody
opens a README to check whether it still describes the thing. These are the
ones a reader would act on, so they are asserted rather than trusted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tools.build_content as bc

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def module_json():
    return json.loads(bc.OUTPUT.read_text(encoding="utf-8"))


def test_the_slide_count_is_right(module_json):
    assert "Thirty-one slides" in README
    assert len(module_json["lessons"]) == 31


def test_the_question_count_is_right(module_json):
    """The bank and the draw are different numbers, and both appear."""
    from server.config import settings
    assert len(module_json["questions"]) == 100
    assert "bank of 100" in README
    assert "ten questions" in README
    assert settings.quiz_length == 10


def test_the_artwork_count_matches_the_slides(module_json):
    assert len(list((ROOT / "assets" / "slides").glob("*.png"))) == \
        len(module_json["lessons"])


def test_the_database_port_matches_the_compose_file():
    """Someone follows these two lines in order; a mismatch stops them on the
    first command they run."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    port = re.search(r'"(\d+):5432"', compose).group(1)
    assert "Postgres on %s" % port in README
    assert port in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_allowlist_is_the_size_the_readme_says():
    from server.content import QUESTION_FIELDS_BEFORE_ANSWER
    assert "allowlist of three fields" in README
    assert len(QUESTION_FIELDS_BEFORE_ANSWER) == 3


def test_the_narration_failure_modes_are_all_still_handled():
    """The README says four, and names them. If one is removed from the
    narration module the README is describing something that is no longer
    there."""
    narration = (ROOT / "frontend" / "src" / "narration.ts").read_text(
        encoding="utf-8")
    assert "four failure modes" in README
    for handled in ("voiceschanged", "KEEPALIVE_MS", "mine !== this.token",
                    "user gesture"):
        assert handled in narration, handled


def test_every_file_the_readme_links_to_exists():
    for link in re.findall(r"\]\(([^)#:]+)\)", README):
        assert (ROOT / link).exists(), link


def test_the_pass_mark_in_the_readme_is_the_one_the_server_uses():
    """A README that says 70% while the server awards at 80% is worse than one
    that says nothing: somebody reads it and believes it."""
    from server.config import settings
    assert "**%d%%**" % round(settings.pass_mark * 100) in README


def test_the_ciso_address_matches_the_default():
    from server.config import settings
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "CERTIFICATE_FROM=%s" % settings.certificate_from in env


def test_the_ports_in_the_readme_are_the_ports_compose_publishes():
    """Somebody follows these in order; a wrong port stops them at the first
    thing they try to open."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = set(re.findall(r'"(\d+):\d+"', compose))
    for port in published:
        assert port in README, "compose publishes %s, README never says so" % port


def test_the_dev_signin_gate_named_in_the_readme_is_the_one_in_the_code():
    from server.config import Settings
    import inspect
    source = inspect.getsource(Settings.__init__)
    assert "ALLOW_DEV_SIGNIN" in source
    assert "ALLOW_DEV_SIGNIN" in README


def test_the_brand_tool_the_readme_names_exists_and_runs():
    from tools import build_brand_assets
    assert "tools/build_brand_assets.py" in README
    assert build_brand_assets.main(["--check"]) == 0


def test_the_pause_lengths_in_the_readme_are_the_ones_in_the_code():
    """Three numbers a reader would quote back at you."""
    import tools.build_content as bc
    assert "%dms after a full stop" % bc.PAUSE_MS["sentence"] in README
    assert "%dms after a colon" % bc.PAUSE_MS["clause"] in README
    assert "%dms between" % bc.PAUSE_MS["paragraph"] in README


def test_the_reporting_thresholds_in_the_readme_are_the_ones_in_the_code():
    from server import reporting
    assert "Below twenty\nanswers" in README
    assert reporting.MIN_ANSWERS == 20


def test_the_grant_command_the_readme_names_exists():
    from server import grant
    assert "server.grant --email" in README
    assert "admin" in grant.ROLES


def test_every_account_flag_the_readme_names_is_a_real_one():
    """Six flags somebody copies out of here at the moment they are least able
    to work out why one of them does not exist."""
    import inspect

    from server import account
    declared = inspect.getsource(account.main)
    for flag in ("--create", "--add-password", "--reset", "--unlock",
                 "--disable", "--list"):
        assert flag in README, "the README does not mention %s" % flag
        assert '"%s"' % flag in declared, "%s is not a flag it accepts" % flag


def test_the_cycle_commands_the_readme_names_are_real():
    import inspect

    from server import cycle
    declared = inspect.getsource(cycle.main)
    for flag in ("--list", "--open", "--due"):
        assert "server.cycle" in README and flag in README, flag
        assert '"%s"' % flag in declared, "%s is not a flag it accepts" % flag


def test_the_password_rules_in_the_readme_are_the_ones_in_the_code():
    """Three numbers somebody will be told over the phone by a security team
    reading this page."""
    from server import auth, passwords
    assert "Ten consecutive failures" in README
    assert auth.LOCKOUT_AFTER == 10
    assert "fifteen minutes" in README
    assert auth.LOCKOUT_MINUTES == 15
    assert "twelve" in README
    assert passwords.MIN_LENGTH == 12
