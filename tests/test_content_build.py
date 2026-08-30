"""The content build refuses to ship content that would be wrong in a browser.

Everything here is a guard against a failure that is silent. A course narrated
one slide out of step, a quiz whose answer is always the third option, an
answer key pointing past the end of its own options — none of these raise an
error at runtime. They render perfectly and measure nothing, and the report
built on top of them reads exactly like a report built on sound content.

So each test damages the authored content in one specific way and asserts the
build refuses it. A guard nobody has watched fail is not a guard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.build_content as bc

ROOT = Path(__file__).resolve().parents[1]
SLIDES = set(range(1, 22))


@pytest.fixture(scope="module")
def module_json() -> dict:
    """The built course, as committed."""
    return json.loads(bc.OUTPUT.read_text(encoding="utf-8"))


@pytest.fixture
def authored() -> dict:
    """The knowledge check as actually written."""
    return json.loads(bc.QUESTIONS.read_text(encoding="utf-8"))


@pytest.fixture
def damaged(tmp_path, monkeypatch, authored):
    """Load a copy of the check with one thing broken in it."""
    target = tmp_path / "knowledge-check.json"
    monkeypatch.setattr(bc, "QUESTIONS", target)

    def load(mutate=None):
        payload = json.loads(json.dumps(authored))
        if mutate:
            mutate(payload["questions"])
        target.write_text(json.dumps(payload), encoding="utf-8")
        return bc.load_questions(SLIDES)

    return load


def refusal(damaged, mutate) -> str:
    with pytest.raises(SystemExit) as exit_info:
        damaged(mutate)
    return str(exit_info.value)


# --------------------------------------------------------------------------
# The authored content, as it stands
# --------------------------------------------------------------------------

def test_the_authored_check_is_usable(damaged):
    assert len(damaged()) == 12


def test_built_module_is_not_stale():
    """The built JSON is committed, so it can drift from its sources. This is
    the same check the build runs with --check, kept here so the drift is
    caught by the test suite rather than by a learner seeing old content."""
    assert bc.main(["--check"]) == 0


def test_every_question_names_the_slide_it_tests():
    """`teaches` is what makes a low score point at the material rather than at
    the people, so an unset one quietly turns a diagnosis into a verdict."""
    module = json.loads(bc.OUTPUT.read_text(encoding="utf-8"))
    slides = {lesson["ordinal"] for lesson in module["lessons"]}
    for question in module["questions"]:
        assert question["teaches"] in slides


def test_answers_use_every_position():
    module = json.loads(bc.OUTPUT.read_text(encoding="utf-8"))
    used = {q["correct_index"] for q in module["questions"]}
    widths = {len(q["options"]) for q in module["questions"]}
    assert used == set(range(max(widths)))


def test_answer_positions_are_not_a_cycle():
    """Balanced is not the same as unguessable: 0,1,2,3,0,1,2,3 uses every
    position equally and can still be scored without reading a word."""
    module = json.loads(bc.OUTPUT.read_text(encoding="utf-8"))
    sequence = [q["correct_index"] for q in module["questions"]]
    for period in (1, 2, 3, 4):
        repeating = [sequence[i % period] for i in range(len(sequence))]
        assert sequence != repeating, (
            "answers repeat with period %d" % period)


# --------------------------------------------------------------------------
# Each way the check can be authored wrongly
# --------------------------------------------------------------------------

def test_answer_key_past_the_end_is_refused(damaged):
    """The worst one: every learner is marked wrong on this question for ever,
    and it reads as a question people find hard."""
    message = refusal(damaged, lambda qs: qs[0].update(correct_index=9))
    assert "graded wrong" in message


def test_teaching_a_slide_that_does_not_exist_is_refused(damaged):
    message = refusal(damaged, lambda qs: qs[1].update(teaches=99))
    assert "does not exist" in message


def test_repeated_option_is_refused(damaged):
    def duplicate(qs):
        qs[2]["options"][0] = qs[2]["options"][1]
    assert "repeats an option" in refusal(damaged, duplicate)


def test_repeated_prompt_is_refused(damaged):
    def same_prompt(qs):
        qs[4]["prompt"] = qs[0]["prompt"]
    assert "repeats an earlier prompt" in refusal(damaged, same_prompt)


def test_question_without_an_explanation_is_refused(damaged):
    """The explanation is the teaching. Without it the quiz says 'incorrect'
    at the one moment the learner is actually paying attention."""
    message = refusal(damaged, lambda qs: qs[3].update(explains=""))
    assert "teaches nothing" in message


def test_single_option_is_refused(damaged):
    message = refusal(damaged, lambda qs: qs[5].update(options=["only one"]))
    assert "fewer than two options" in message


def test_unused_answer_position_is_refused(damaged):
    """'The answer is never the last one' is a rule that can be learned
    instead of the material."""
    def never_last(qs):
        for question in qs:
            question["correct_index"] = 0
    message = refusal(damaged, never_last)
    assert "never the correct answer" in message


def test_dominant_answer_position_is_refused(damaged):
    def mostly_one(qs):
        for question in qs[:7]:
            question["correct_index"] = 1
    message = refusal(damaged, mostly_one)
    assert "guessing it beats knowing" in message


def test_odd_one_out_phrasing_is_refused(damaged):
    """Three distractors phrased alike and an answer that is not makes the
    answer findable without reading past the third word."""
    def matching_distractors(qs):
        question = qs[0]
        answer = question["options"][question["correct_index"]]
        question["options"] = [
            answer,
            "It is fine to ignore, part one",
            "It is fine to ignore, part two",
            "It is fine to ignore, part three",
        ]
        question["correct_index"] = 0
    message = refusal(damaged, matching_distractors)
    assert "odd one out" in message


# --------------------------------------------------------------------------
# Slides and narration
# --------------------------------------------------------------------------

def test_every_slide_is_narrated_or_says_why_not():
    module = json.loads(bc.OUTPUT.read_text(encoding="utf-8"))
    for lesson in module["lessons"]:
        assert lesson["narration"] or lesson["silent_because"], (
            "slide %d is silent and does not say why" % lesson["ordinal"])


def test_narration_without_artwork_is_refused(monkeypatch):
    """A script with no slide shifts every slide after it, which is silent and
    wrong from that point to the end of the course."""
    scripts = bc.parse_scripts()
    scripts[99] = ("Orphan", "narration with no slide behind it")
    monkeypatch.setattr(bc, "parse_scripts", lambda: scripts)
    with pytest.raises(SystemExit) as exit_info:
        bc.build()
    assert "no artwork" in str(exit_info.value)


def test_artwork_without_narration_is_refused(monkeypatch):
    slides = bc.parse_slides()
    slides[98] = ("orphan", "98-orphan.png")
    monkeypatch.setattr(bc, "parse_slides", lambda: slides)
    with pytest.raises(SystemExit) as exit_info:
        bc.build()
    assert "INTENTIONALLY_SILENT" in str(exit_info.value)


def test_mismatched_titles_are_refused(monkeypatch):
    """The numbers can line up while the pairing is wrong."""
    scripts = bc.parse_scripts()
    scripts[3] = ("Completely Unrelated Heading", scripts[3][1])
    monkeypatch.setattr(bc, "parse_scripts", lambda: scripts)
    with pytest.raises(SystemExit) as exit_info:
        bc.build()
    assert "share no word" in str(exit_info.value)


def test_verified_pairings_each_carry_a_reason():
    """The allowlist exists so a pairing nobody checked still fails. An entry
    with no reason is indistinguishable from one added to silence the guard."""
    for number, reason in bc.VERIFIED_PAIRINGS.items():
        assert len(reason) > 20, "pairing %d has no real reason" % number
    for number, reason in bc.INTENTIONALLY_SILENT.items():
        assert len(reason) > 20, "slide %d has no real reason" % number


# --------------------------------------------------------------------------
# The silence between sentences
# --------------------------------------------------------------------------

def test_the_pause_lengths_match_the_ones_the_player_uses():
    """The silence happens in the browser and is counted here. Two copies of
    the same number is one copy too many, so this fails when they drift."""
    import re
    source = (ROOT / "frontend" / "src" / "narration.ts").read_text(
        encoding="utf-8")
    block = re.search(r"export const PAUSE = \{(.*?)\n\}", source, re.S).group(1)
    in_player = {name: int(value)
                 for name, value in re.findall(r"(\w+):\s*(\d+),", block)}
    assert in_player == bc.PAUSE_MS


def test_a_slide_is_reported_as_longer_than_its_words_alone(module_json):
    """Reporting only the word count tells a learner the course is two
    minutes shorter than it is, every time they look at it."""
    for lesson in module_json["lessons"]:
        if not lesson["narration"]:
            continue
        words = len(lesson["narration"].split())
        spoken = words / bc.WORDS_PER_MINUTE * 60
        assert lesson["narration_seconds"] > spoken
        assert lesson["narration_seconds"] == round(
            spoken + bc.pause_seconds(lesson["narration"]))


def test_the_silence_is_not_waited_out_after_the_last_sentence():
    assert bc.pause_seconds("One. Two. Three.") == pytest.approx(
        bc.PAUSE_MS["sentence"] * 2 / 1000)
    assert bc.pause_seconds("Only one sentence.") == 0


def test_nothing_in_the_script_would_be_mis_split_into_two_sentences(module_json):
    """The splitter is deliberately simple because the material is: British
    prose with no abbreviations, no decimals and no ellipses. If any appear,
    "e.g." becomes two sentences with a pause in the middle — so this fails
    rather than the narration quietly developing a stutter."""
    import re
    hazards = {
        "an abbreviation": r"\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|Prof|St|"
                           r"etc|eg|ie|vs|approx|No)\.",
        "a decimal number": r"\d\.\d",
        "an ellipsis": r"\.\.\.|…",
        # A full stop followed by a lower-case letter is either an
        # abbreviation or a typo; both read badly once there is a pause there.
        "a full stop mid-sentence": r"\.\s+[a-z]",
    }
    for lesson in module_json["lessons"]:
        for description, pattern in hazards.items():
            found = re.findall(pattern, lesson["narration"])
            assert not found, ("slide %d contains %s (%r), which the sentence "
                               "splitter would get wrong"
                               % (lesson["ordinal"], description, found[:3]))
