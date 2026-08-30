"""Taking in narration recorded elsewhere.

Somebody drops twenty files into a folder and runs one command. What they need
back is a clear account of what was matched and what was not — because the
failure mode is a slide quietly playing nothing, or worse, playing the wrong
slide's audio, and neither announces itself.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from tools import import_narration as importer

LESSONS = [
    {"ordinal": 1, "image": "slides/01-title.png", "narration": "Welcome. Let us begin."},
    {"ordinal": 3, "image": "slides/03-phishing.png",
     "narration": "Phishing is the most common way in. Read the address first."},
    {"ordinal": 11, "image": "slides/11-ransomware-action.png",
     "narration": "Disconnect from the network. Do not power the machine off."},
]


def silence(path: Path, seconds: float = 2.0) -> Path:
    """A real, playable wav of the requested length."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * int(8000 * seconds))
    return path


# ── working out which slide a file is for ──────────────────────────────────

@pytest.mark.parametrize("name", [
    "03.mp3", "3.mp3", "slide-03.mp3", "slide 3.mp3",
    "03 - Phishing Emails.mp3", "03-phishing.m4a",
])
def test_a_number_in_the_name_finds_the_slide(name):
    assert importer.match_slide(name, LESSONS) == [3]


def test_a_file_with_no_number_is_matched_on_the_artwork(name="phishing.mp3"):
    assert importer.match_slide(name, LESSONS) == [3]


def test_a_name_that_means_nothing_matches_nothing():
    assert importer.match_slide("final-mix-v2.mp3", LESSONS) == []
    assert importer.match_slide("99.mp3", LESSONS) == []


def test_a_name_that_could_be_two_slides_is_ambiguous():
    """Better reported than guessed: the wrong slide's audio is worse than
    none, and nothing about it looks wrong from the outside."""
    lessons = LESSONS + [{"ordinal": 4, "image": "slides/04-phishing-signs.png",
                          "narration": "Look at the address."}]
    assert len(importer.match_slide("phishing.mp3", lessons)) == 2


# ── reading the folder ─────────────────────────────────────────────────────

def test_matched_files_are_measured_and_stamped(tmp_path):
    silence(tmp_path / "01.wav", 3.0)
    silence(tmp_path / "03.wav", 4.5)
    slides, notes = importer.scan(tmp_path, LESSONS)

    assert set(slides) == {"1", "3"}
    assert slides["1"]["seconds"] == 3.0
    assert slides["3"]["seconds"] == 4.5
    assert slides["1"]["file"] == "01.wav"
    # The hash is the assertion that this file is a recording of THESE words.
    assert slides["1"]["script_sha"] == importer.script_hash(LESSONS[0]["narration"])
    assert not notes


def test_the_supplied_filenames_are_kept(tmp_path):
    """Renaming somebody's files under them is rude and makes the folder
    stop matching whatever they generated it from."""
    silence(tmp_path / "03 - Phishing Emails.wav")
    slides, _ = importer.scan(tmp_path, LESSONS)
    assert slides["3"]["file"] == "03 - Phishing Emails.wav"


def test_a_file_naming_no_slide_is_reported(tmp_path):
    silence(tmp_path / "01.wav")
    silence(tmp_path / "outtake.wav")
    slides, notes = importer.scan(tmp_path, LESSONS)
    assert set(slides) == {"1"}
    assert any("outtake.wav" in note and "does not name a slide" in note
               for note in notes)


def test_a_format_safari_will_not_play_is_reported(tmp_path):
    """Silence on one browser and not another is the sort of thing found by a
    learner rather than by whoever uploaded it."""
    silence(tmp_path / "01.ogg")
    _, notes = importer.scan(tmp_path, LESSONS)
    assert any("Safari" in note for note in notes)


def test_two_files_claiming_one_slide_is_reported(tmp_path):
    silence(tmp_path / "01.wav")
    silence(tmp_path / "slide-1-final.wav")
    _, notes = importer.scan(tmp_path, LESSONS)
    assert any("claiming it" in note for note in notes)


def test_an_ambiguous_file_is_given_to_no_slide_at_all(tmp_path):
    """Reported and skipped, not handed to whichever slide sorted first. The
    wrong slide's audio is worse than none and nothing about it looks wrong."""
    lessons = LESSONS + [{"ordinal": 4, "image": "slides/04-phishing-signs.png",
                          "narration": "Look at the address."}]
    silence(tmp_path / "phishing.wav")
    slides, notes = importer.scan(tmp_path, lessons)
    assert "3" not in slides and "4" not in slides
    assert any("ambiguous" in note for note in notes)


def test_a_slide_with_no_file_simply_has_no_recording(tmp_path):
    silence(tmp_path / "01.wav")
    slides, _ = importer.scan(tmp_path, LESSONS)
    assert "3" not in slides and "11" not in slides


# ── timing sidecars ────────────────────────────────────────────────────────

def vtt(cues: list[tuple[float, float, str]]) -> str:
    def stamp(seconds: float) -> str:
        return "%02d:%02d:%06.3f" % (seconds // 3600, seconds // 60 % 60,
                                     seconds % 60)
    lines = ["WEBVTT", ""]
    for start, end, text in cues:
        lines += ["%s --> %s" % (stamp(start), stamp(end)), text, ""]
    return "\n".join(lines)


def test_subtitles_beside_the_audio_become_a_word_track(tmp_path):
    silence(tmp_path / "11.wav", 6.0)
    (tmp_path / "11.vtt").write_text(vtt([
        (0.0, 3.0, "Disconnect from the network."),
        (3.0, 6.0, "Do not power the machine off."),
    ]), encoding="utf-8")

    slides, notes = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["timings"] == "11.timings.json"
    marks = json.loads((tmp_path / "11.timings.json").read_text(encoding="utf-8"))

    narration = LESSONS[2]["narration"]
    assert len(marks) == len(narration.split())
    for mark in marks:
        assert narration[mark["at"]:mark["at"] + mark["len"]].strip()
    assert marks == sorted(marks, key=lambda m: m["ms"])
    assert marks[-1]["ms"] <= 6000


def test_a_cue_anchors_the_words_inside_it(tmp_path):
    """Words are spread across a cue by length, which is an approximation —
    but every cue re-synchronises, so it cannot drift. Spreading words across
    a whole minute-long slide would, which is why that is never done."""
    silence(tmp_path / "11.wav", 6.0)
    (tmp_path / "11.vtt").write_text(vtt([
        (0.0, 3.0, "Disconnect from the network."),
        (3.0, 6.0, "Do not power the machine off."),
    ]), encoding="utf-8")
    importer.scan(tmp_path, LESSONS)
    marks = json.loads((tmp_path / "11.timings.json").read_text(encoding="utf-8"))

    narration = LESSONS[2]["narration"]
    second = narration.index("Do not power")
    starts_second_cue = [m for m in marks if m["at"] >= second]
    assert starts_second_cue[0]["ms"] >= 3000
    assert all(m["ms"] < 3000 for m in marks if m["at"] < second)


def test_subrip_works_as_well_as_webvtt(tmp_path):
    silence(tmp_path / "11.wav", 6.0)
    (tmp_path / "11.srt").write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nDisconnect from the network.\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\nDo not power the machine off.\n",
        encoding="utf-8")
    slides, _ = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["timings"]
    assert slides["11"]["words"] == len(LESSONS[2]["narration"].split())


def test_subtitles_of_a_different_script_are_refused(tmp_path):
    """A sidecar from the wrong take would highlight words the voice is not
    saying, all the way down the slide."""
    silence(tmp_path / "11.wav", 6.0)
    (tmp_path / "11.vtt").write_text(vtt([
        (0.0, 3.0, "Something else entirely."),
        (3.0, 6.0, "Nothing to do with this slide."),
    ]), encoding="utf-8")
    slides, notes = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["timings"] == ""
    assert any("does not line up" in note for note in notes)


def test_a_sidecar_that_drifts_part_way_through_is_refused(tmp_path):
    """The first words match and later ones do not — a take that was edited
    after the subtitles were exported. Caught by the per-word check rather
    than by the cue's opening word, which is what makes it worth its own
    test: the two are separate guards and only one of them was exercised."""
    silence(tmp_path / "11.wav", 6.0)
    (tmp_path / "11.vtt").write_text(vtt([
        (0.0, 3.0, "Disconnect from the network."),
        (3.0, 6.0, "Do not power the aeroplane off."),
    ]), encoding="utf-8")
    slides, notes = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["timings"] == ""
    assert any("does not line up" in note for note in notes)


def test_a_track_running_past_its_own_audio_is_refused(tmp_path):
    silence(tmp_path / "11.wav", 2.0)
    (tmp_path / "11.vtt").write_text(vtt([
        (0.0, 30.0, "Disconnect from the network."),
        (30.0, 60.0, "Do not power the machine off."),
    ]), encoding="utf-8")
    slides, _ = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["timings"] == ""


def test_audio_with_no_sidecar_still_plays(tmp_path):
    silence(tmp_path / "11.wav", 6.0)
    slides, notes = importer.scan(tmp_path, LESSONS)
    assert slides["11"]["file"] == "11.wav"
    assert slides["11"]["timings"] == ""
    assert not notes


# ── the same words, everywhere ─────────────────────────────────────────────

def test_the_hash_agrees_with_the_other_two_places_it_is_computed():
    from server import ingest
    from tools import build_narration
    for text in ("One thing.", "Line one.\nLine two.", "  padded  "):
        assert importer.script_hash(text) == build_narration.script_hash(text)
        assert importer.script_hash(text) == ingest.script_hash(text)


def test_the_generator_refuses_to_overwrite_supplied_recordings(tmp_path):
    """Synthesising over a real voice, because a hash happened not to match,
    would only be discovered by listening to the whole course again."""
    from tools import build_narration
    folder = tmp_path / "a-module"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"backend": "imported", "slides": {}}), encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        build_narration.record({"lessons": LESSONS}, build_narration.Sapi(),
                               folder, force=False)
    assert "supplied, not generated" in str(refused.value)
