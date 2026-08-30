"""Authored content, and what a browser is allowed to see of it.

The course is authored as files and built into `data/modules/*.json` by
`tools.build_content`. This module reads those files and is the one place that
decides which fields of a question may cross to the client.

THAT DECISION IS AN ALLOWLIST, NOT A BLACKLIST. "Send everything except
`correct_index`" is one new field away from shipping the answers, and the
failure is invisible from the outside: the quiz still looks and behaves
exactly as it should, it is simply answerable by anyone who opens the network
tab. So a question is rebuilt field by field on the way out, and anything not
named below does not leave the server.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.config import settings

#: Everything a learner may see BEFORE they answer. Deliberately short.
QUESTION_FIELDS_BEFORE_ANSWER = ("ordinal", "prompt", "options")

#: What a module file must contain to be loadable at all.
REQUIRED_MODULE_FIELDS = ("slug", "title", "content_hash", "lessons",
                          "questions")


def module_files() -> List[Path]:
    return sorted(Path(settings.content_dir).glob("*.json"))


def load_module_file(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_MODULE_FIELDS if f not in payload]
    if missing:
        raise ValueError(
            "%s is missing %s. It was probably hand-edited; rebuild it with "
            "`python -m tools.build_content`." % (path.name, missing))
    if not payload["lessons"]:
        raise ValueError("%s has no lessons" % path.name)
    return payload


def load_modules() -> List[Dict[str, Any]]:
    """Every authored module, in file order."""
    return [load_module_file(p) for p in module_files()]


def question_for_learner(question: Dict[str, Any]) -> Dict[str, Any]:
    """A question with the answer removed — by construction, not by deletion.

    Built from an allowlist so that a field added to the authored content, or
    to the database row, does not reach the browser until somebody adds it
    here on purpose.
    """
    return {field: question[field] for field in QUESTION_FIELDS_BEFORE_ANSWER}


def reveal(question: Dict[str, Any], chosen_index: Optional[int]
           ) -> Dict[str, Any]:
    """What the learner sees AFTER answering.

    The explanation is the part that does the teaching, so this is the one
    moment the answer should be shown — right or wrong, and whichever way they
    went. `teaches` comes back too, so the player can offer the slide again.
    """
    correct_index = question["correct_index"]
    return {
        "ordinal": question["ordinal"],
        "correct": chosen_index == correct_index,
        "correct_index": correct_index,
        "explains": question.get("explains", ""),
        "teaches": question.get("teaches"),
    }
