"""Take in narration recorded elsewhere, and attach it to the slides.

Drop the audio into `assets/narration/<module-slug>/` and run this. It works
out which slide each file belongs to, reads how long it runs, picks up any
timing sidecar next to it, and writes the manifest the server reads.

    python -m tools.import_narration
    python -m tools.import_narration --check     # exit 1 if anything is amiss

NAMING. A file is matched to a slide by the first number in its name, so
`01.mp3`, `1.mp3`, `slide-01.mp3` and `03 - Phishing Emails.mp3` all work. A
file with no number is matched on the artwork name instead, so `phishing.mp3`
finds the slide whose image is `03-phishing.png`. Anything that matches
nothing, or two things, is reported rather than guessed at.

WHAT THIS WRITES DOWN, AND WHY. Each entry records the SHA-256 of the words
the slide says TODAY. That is an assertion — importing a file is a person
saying "this is a recording of this script" — and it is what makes staleness
detectable later: edit the script without re-recording and the hash stops
matching, so the server drops the recording and the browser reads the slide
instead. A worse voice, rather than a voice saying something the transcript on
screen does not.

TIMING SIDECARS ARE OPTIONAL. A file named like the audio but ending .vtt,
.srt or .json is read as the word track that makes the transcript follow along.
Most text-to-speech tools can export subtitles beside the audio. Without one
the slide plays perfectly well; the transcript simply does not highlight.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "data" / "modules"
NARRATION = ROOT / "assets" / "narration"

MANIFEST = "manifest.json"

#: Formats a browser will play. mp3 and m4a work everywhere; the rest are
#: accepted because somebody will have them, and warned about because Safari
#: will not play ogg or opus and the learner would simply hear nothing.
AUDIO = {".mp3": True, ".m4a": True, ".aac": True, ".wav": True,
         ".ogg": False, ".opus": False, ".flac": False, ".webm": False}

SIDECARS = (".json", ".vtt", ".srt")


def script_hash(text: str) -> str:
    """Mirrors `script_hash` in tools/build_narration.py and server/ingest.py."""
    import hashlib
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# ── how long is it ─────────────────────────────────────────────────────────

def duration_seconds(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as audio:
                return audio.getnframes() / audio.getframerate()
        except wave.Error:
            pass                        # a wav container mutagen may still read
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        raise SystemExit(
            "mutagen is needed to read how long an audio file runs.\n"
            "  pip install mutagen")
    audio = MutagenFile(str(path))
    if audio is None or not getattr(audio, "info", None):
        raise SystemExit("cannot read %s — is it really audio?" % path.name)
    return float(audio.info.length)


# ── which slide is it ──────────────────────────────────────────────────────

def match_slide(name: str, lessons: list[dict]) -> list[int]:
    """The slides a filename could be for. More than one means ambiguous."""
    stem = Path(name).stem
    numbers = re.findall(r"\d+", stem)
    if numbers:
        ordinal = int(numbers[0])
        return [ordinal] if any(l["ordinal"] == ordinal for l in lessons) else []

    # No number: try the artwork name, so `phishing.mp3` finds 03-phishing.png.
    words = set(re.findall(r"[a-z]+", stem.lower()))
    if not words:
        return []
    hits = []
    for lesson in lessons:
        slug = Path(lesson.get("image", "")).stem
        art = set(re.findall(r"[a-z]+", re.sub(r"^\d+-", "", slug).lower()))
        if art and art & words:
            hits.append(lesson["ordinal"])
    return hits


# ── timing sidecars ────────────────────────────────────────────────────────

_TIMESTAMP = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")


def _ms(hours: str, minutes: str, seconds: str, fraction: str) -> int:
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 \
        + int(fraction.ljust(3, "0"))


def parse_cues(text: str) -> list[tuple[int, int, str]]:
    """(start ms, end ms, words) out of a WebVTT or SubRip file."""
    cues, pending, spoken = [], None, []
    for line in text.splitlines():
        stamp = _TIMESTAMP.search(line)
        if stamp:
            if pending and spoken:
                cues.append((pending[0], pending[1], " ".join(spoken)))
            pending, spoken = (_ms(*stamp.groups()[:4]),
                               _ms(*stamp.groups()[4:])), []
            continue
        stripped = line.strip()
        if not stripped or stripped.isdigit() or stripped.startswith("WEBVTT"):
            continue
        if pending:
            # Strip the inline markup subtitle formats allow.
            spoken.append(re.sub(r"<[^>]+>", "", stripped))
    if pending and spoken:
        cues.append((pending[0], pending[1], " ".join(spoken)))
    return cues


def marks_from_cues(narration: str, cues: list[tuple[int, int, str]]) -> list[dict]:
    """Word marks from subtitle cues.

    Each cue's start and end are ground truth, so words INSIDE a cue are
    spread across it by their length. That is an approximation, but a bounded
    one — a couple of seconds of speech — and it cannot drift, because every
    cue re-synchronises. Spreading words across a whole minute-long slide
    would drift, which is why that is not done anywhere.

    A cue whose words cannot be found in the script means the sidecar is not
    of this narration, and nothing is returned rather than something wrong.
    """
    marks: list[dict] = []
    cursor = 0
    flat = narration.lower()
    for start, end, text in cues:
        words = re.findall(r"\S+", text)
        if not words:
            continue
        # Find this cue's first and last word in the script, in order.
        first = flat.find(re.sub(r"[^\w']+$", "", words[0]).lower(), cursor)
        if first < 0:
            return []
        spans = []
        at = first
        for word in words:
            bare = re.sub(r"^[^\w']+|[^\w']+$", "", word).lower()
            if not bare:
                continue
            found = flat.find(bare, at)
            if found < 0 or found - at > 80:
                return []               # the sidecar does not fit the script
            spans.append((found, len(bare)))
            at = found + len(bare)
        if not spans:
            continue
        cursor = at

        # Spread the cue's own span across its words, by where each falls.
        first_at, last = spans[0][0], spans[-1][0] + spans[-1][1]
        width = max(1, last - first_at)
        for offset, length in spans:
            share = (offset - first_at) / width
            marks.append({"ms": int(start + (end - start) * share),
                          "at": offset, "len": length})
    return marks


def read_sidecar(path: Path, narration: str) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        loaded = json.loads(text)
        if isinstance(loaded, list) and all(
                isinstance(m, dict) and {"ms", "at", "len"} <= set(m)
                for m in loaded):
            return loaded
        return []
    return marks_from_cues(narration, parse_cues(text))


# ── the work ───────────────────────────────────────────────────────────────

def scan(folder: Path, lessons: list[dict]) -> tuple[dict, list[str]]:
    """Match every audio file in the folder to a slide."""
    from tools.build_narration import usable_marks

    by_ordinal: dict[int, Path] = {}
    notes: list[str] = []
    clashes: dict[int, list[str]] = {}

    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in AUDIO:
            continue
        if not AUDIO[path.suffix.lower()]:
            notes.append("%s is a %s file, which Safari will not play — the "
                         "learner would hear nothing. Prefer mp3 or m4a."
                         % (path.name, path.suffix.lstrip(".")))
        hits = match_slide(path.name, lessons)
        if not hits:
            notes.append("%s does not name a slide. Put the slide number in "
                         "the filename, like 03-whatever%s."
                         % (path.name, path.suffix))
            continue
        if len(hits) > 1:
            notes.append("%s could be slide %s — ambiguous, so it was skipped."
                         % (path.name, " or ".join(map(str, hits))))
            continue
        clashes.setdefault(hits[0], []).append(path.name)
        by_ordinal[hits[0]] = path

    for ordinal, names in sorted(clashes.items()):
        if len(names) > 1:
            notes.append("slide %d has %d files claiming it (%s); using %s."
                         % (ordinal, len(names), ", ".join(names), names[-1]))

    slides = {}
    for lesson in lessons:
        path = by_ordinal.get(lesson["ordinal"])
        if path is None:
            continue
        seconds = duration_seconds(path)

        track, marks = "", []
        for suffix in SIDECARS:
            candidate = path.with_suffix(suffix)
            if not candidate.exists():
                continue
            marks = usable_marks(lesson["narration"],
                                 read_sidecar(candidate, lesson["narration"]),
                                 seconds)
            if marks:
                track = "%02d.timings.json" % lesson["ordinal"]
                (folder / track).write_text(
                    json.dumps(marks, separators=(",", ":")), encoding="utf-8")
            else:
                notes.append("%s does not line up with slide %d's script, so "
                             "the transcript will not follow the audio."
                             % (candidate.name, lesson["ordinal"]))
            break

        slides[str(lesson["ordinal"])] = {
            "file": path.name,
            "timings": track,
            "script_sha": script_hash(lesson["narration"]),
            "seconds": round(seconds, 2),
            "words": len(marks),
        }
    return slides, notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report without writing anything")
    args = parser.parse_args(argv)

    modules = sorted(CONTENT.glob("*.json"))
    if not modules:
        raise SystemExit("no built content; run python -m tools.build_content")

    problems = 0
    for path in modules:
        module = json.loads(path.read_text(encoding="utf-8"))
        lessons = [l for l in module["lessons"] if l.get("narration")]
        folder = NARRATION / module["slug"]
        print("%s — %d slides need narration" % (module["slug"], len(lessons)))

        if not folder.is_dir():
            print("  nothing in %s yet. Drop the audio there and run this again."
                  % folder.relative_to(ROOT))
            problems += 1
            continue

        slides, notes = scan(folder, lessons)
        for note in notes:
            print("  ! " + note)

        missing = [l["ordinal"] for l in lessons if str(l["ordinal"]) not in slides]
        total = sum(entry["seconds"] for entry in slides.values())
        print("  matched %d of %d slides, %.0f minutes of audio"
              % (len(slides), len(lessons), total / 60))
        with_track = sum(1 for e in slides.values() if e["timings"])
        print("  %d with a word track (the transcript follows those)"
              % with_track)
        if missing:
            print("  no audio for slide(s) %s — the browser will read them."
                  % missing)
        if notes or missing:
            problems += 1

        if args.check:
            continue
        (folder / MANIFEST).write_text(json.dumps({
            "backend": "imported",
            "voice": "supplied recordings",
            "slides": slides,
            "_about": [
                "Narration recorded elsewhere and imported by",
                "tools/import_narration.py.",
                "",
                "`script_sha` is the SHA-256 of the words each file was",
                "imported against. Importing is an assertion that the file is",
                "a recording of that script; the server attaches a recording",
                "only while the hash still matches, so editing the script",
                "without re-recording falls back to the browser rather than",
                "playing something the transcript contradicts.",
            ],
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("  wrote %s" % (folder / MANIFEST).relative_to(ROOT))

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
