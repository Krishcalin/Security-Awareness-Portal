"""Record the narration, so the course sounds the same for everybody.

The browser's own speech synthesis was the wrong call. It sounds like whatever
voice happens to be installed — on this machine, two legacy American voices
reading British spelling — it clips the start of every utterance on Chrome for
Windows, and it means no two employees hear the same course, which is a strange
property for something that issues a certificate.

So the narration is recorded once, from the authored script, and served.

THE ONE RISK THIS HAS TO CLOSE. Audio files go stale. The script gets edited,
the recording does not, and a learner hears one wording while reading another
on screen — which is worse than a robotic voice, and completely invisible to
whoever made the edit. So every recording stores the SHA-256 of the exact text
it was made from, and `tools/build_content.py` attaches a recording to a slide
only when that hash still matches. Drift cannot produce wrong audio; it can
only fall back to the synthesiser, which is a bad voice rather than a lie.

    python -m tools.build_narration                 # record what is stale
    python -m tools.build_narration --force         # record everything again
    python -m tools.build_narration --check         # exit 1 if any is stale
    python -m tools.build_narration --backend azure # the good voices

Two backends:

  sapi   Windows' built-in synthesiser. Needs no account and no key, writes
         WAV, and sounds exactly as ordinary as the thing being replaced. It
         exists to prove the pipeline end to end — file playback, word
         timings, the staleness guard — before anybody pays for anything. Its
         output is not committed.

  azure  Azure Neural TTS. The reason for doing this at all. Writes MP3,
         reports word boundaries, and costs about 30 cents to record the whole
         course. Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "data" / "modules"
NARRATION = ROOT / "assets" / "narration"

#: British English, because the script is. The whole point of recording is that
#: this is decided once here rather than by whatever each laptop has installed.
AZURE_VOICE = "en-GB-SoniaNeural"

MANIFEST = "manifest.json"


def script_hash(text: str) -> str:
    """What a recording is a recording OF. Whitespace is normalised so that
    re-wrapping a paragraph does not invalidate a perfectly good take."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# ── backends ───────────────────────────────────────────────────────────────

class Sapi:
    """Windows' built-in synthesiser, via PowerShell."""

    name = "sapi"
    extension = "wav"

    def __init__(self, voice: str = ""):
        self.voice = voice

    def describe(self) -> str:
        return "windows sapi" + (" / " + self.voice if self.voice else "")

    def speak(self, text: str, destination: Path) -> list[dict]:
        script = Path(__file__).with_name("_sapi_record.ps1")
        with tempfile.TemporaryDirectory() as work:
            text_file = Path(work) / "script.txt"
            timings_file = Path(work) / "timings.json"
            text_file.write_text(text, encoding="utf-8")
            command = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script),
                "-Text", str(text_file),
                "-Wav", str(destination),
                "-Timings", str(timings_file),
            ]
            if self.voice:
                command += ["-Voice", self.voice]
            done = subprocess.run(command, capture_output=True, text=True)
            if done.returncode != 0 or not destination.exists():
                raise SystemExit("sapi recording failed:\n" + done.stderr.strip())
            marks = json.loads(timings_file.read_text(encoding="utf-8-sig"))
        # A single-word utterance comes back as an object rather than a list.
        if isinstance(marks, dict):
            marks = [marks]
        return [{"ms": m["ms"], "at": m["at"], "len": m["len"]} for m in marks]


class Azure:
    """Azure Neural TTS."""

    name = "azure"
    extension = "mp3"

    def __init__(self, voice: str = AZURE_VOICE):
        self.voice = voice
        self.key = os.environ.get("AZURE_SPEECH_KEY", "")
        self.region = os.environ.get("AZURE_SPEECH_REGION", "")
        if not self.key or not self.region:
            raise SystemExit(
                "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are not set. The "
                "sapi backend needs neither and proves the pipeline; this one "
                "is for the voice people will actually hear.")

    def describe(self) -> str:
        return "azure / " + self.voice

    def speak(self, text: str, destination: Path) -> list[dict]:
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise SystemExit(
                "azure-cognitiveservices-speech is not installed. "
                "pip install azure-cognitiveservices-speech")

        config = speechsdk.SpeechConfig(subscription=self.key,
                                        region=self.region)
        config.speech_synthesis_voice_name = self.voice
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)
        config.request_word_level_timestamps()

        audio = speechsdk.audio.AudioOutputConfig(filename=str(destination))
        synthesiser = speechsdk.SpeechSynthesizer(speech_config=config,
                                                  audio_config=audio)
        marks: list[dict] = []
        synthesiser.synthesis_word_boundary.connect(
            lambda event: marks.append({
                # Ticks are 100ns.
                "ms": int(event.audio_offset / 10_000),
                "at": event.text_offset,
                "len": event.word_length,
            }))

        result = synthesiser.speak_text_async(text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            raise SystemExit("azure synthesis failed: %s" % result.reason)
        return marks


BACKENDS = {"sapi": Sapi, "azure": Azure}


# ── word tracks ────────────────────────────────────────────────────────────

def usable_marks(text: str, marks: list[dict], seconds: float) -> list[dict]:
    """The word track, if it can be believed. Otherwise nothing.

    Measured on the Windows synthesiser, its own report is wrong in two
    independent ways on a minute of speech: the timeline runs about a third
    long — 78 seconds claimed for a 58 second file — and the character offsets
    drift, reaching twenty-one characters adrift by the end, so a mark that
    should point at "you" points into the middle of "that works". It also
    emits fewer marks than there are words.

    A track that is approximately right is worse than none: the highlight
    creeps ahead of the voice and the learner ends up distrusting the reading
    rather than the highlight. So this checks and discards, and a slide with
    no track simply plays without one.
    """
    if not marks:
        return []
    if any(b["ms"] < a["ms"] for a, b in zip(marks, marks[1:])):
        return []                       # not monotonic
    if marks[-1]["ms"] > (seconds + 1) * 1000:
        return []                       # runs past the end of its own audio
    for mark in marks:
        word = text[mark["at"]:mark["at"] + mark["len"]]
        if not word.strip():
            return []                   # points at whitespace, or past the end
    return marks


# ── durations ──────────────────────────────────────────────────────────────

def duration_seconds(path: Path, marks: list[dict]) -> float:
    """How long the recording runs.

    Read out of a WAV header where there is one. MP3 has no cheap header to
    trust, and pulling in a decoder to learn something the timings already
    imply is not worth it — the last word's start plus a beat is close enough
    for a figure shown as "about N seconds".
    """
    if path.suffix.lower() == ".wav":
        with wave.open(str(path)) as audio:
            return audio.getnframes() / audio.getframerate()
    if marks:
        return marks[-1]["ms"] / 1000 + 0.6
    return 0.0


# ── the work ───────────────────────────────────────────────────────────────

def lessons_of(module: dict) -> list[dict]:
    return [l for l in module["lessons"] if l.get("narration")]


def load_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    if not path.exists():
        return {"slides": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def stale(module: dict, manifest: dict, folder: Path) -> list[dict]:
    """Slides whose recording is missing, or is of different words."""
    out = []
    for lesson in lessons_of(module):
        recorded = manifest.get("slides", {}).get(str(lesson["ordinal"]))
        if not recorded:
            out.append(lesson)
            continue
        if recorded["script_sha"] != script_hash(lesson["narration"]):
            out.append(lesson)
            continue
        if not (folder / recorded["file"]).exists():
            out.append(lesson)
            continue
        if recorded["timings"] and not (folder / recorded["timings"]).exists():
            out.append(lesson)
    return out


def record(module: dict, backend, folder: Path, force: bool) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(folder)
    if manifest.get("backend") == "imported" and not force:
        # Somebody supplied these recordings. Synthesising over the top of
        # them — because a hash happened not to match, say — would silently
        # replace a real voice with a machine one, and the only way anybody
        # would find out is by listening to the whole course again.
        raise SystemExit(
            "the narration for %s was supplied, not generated.\n"
            "  To re-read the supplied files: python -m tools.import_narration\n"
            "  To synthesise over the top anyway: --force" % folder.name)
    if force or manifest.get("backend") != backend.name:
        # A half-and-half course, recorded in two voices, is worse than either.
        manifest = {"slides": {}}

    todo = lessons_of(module) if force else stale(module, manifest, folder)
    if not todo:
        print("every slide is already recorded from the current script.")
        return manifest

    print("recording %d slide(s) with %s" % (len(todo), backend.describe()))
    slides = manifest.setdefault("slides", {})
    for lesson in todo:
        name = "%02d.%s" % (lesson["ordinal"], backend.extension)
        destination = folder / name
        raw = backend.speak(lesson["narration"], destination)
        seconds = duration_seconds(destination, raw)
        marks = usable_marks(lesson["narration"], raw, seconds)

        track = "%02d.timings.json" % lesson["ordinal"]
        track_path = folder / track
        if marks:
            track_path.write_text(json.dumps(marks, separators=(",", ":")),
                                  encoding="utf-8")
        else:
            track_path.unlink(missing_ok=True)

        slides[str(lesson["ordinal"])] = {
            "file": name,
            "timings": track if marks else "",
            "script_sha": script_hash(lesson["narration"]),
            "seconds": round(seconds, 2),
            "words": len(marks),
        }
        print("  slide %2d  %6.1f s  %s  %s"
              % (lesson["ordinal"], seconds,
                 "%4d word marks" % len(marks) if marks
                 else "no usable word track",
                 name))

    manifest.update({
        "backend": backend.name,
        "voice": backend.describe(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_about": [
            "Recorded narration. Generated by tools/build_narration.py.",
            "",
            "`script_sha` is the SHA-256 of the words each file was recorded",
            "from, with whitespace normalised. tools/build_content.py attaches",
            "a recording to a slide ONLY when that hash still matches, so an",
            "edited script cannot leave a learner hearing one thing while",
            "reading another - it falls back to the browser's synthesiser",
            "instead, which is a worse voice rather than a wrong one.",
        ],
    })
    (folder / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="sapi")
    parser.add_argument("--voice", default="",
                        help="backend-specific voice name")
    parser.add_argument("--force", action="store_true",
                        help="re-record every slide, not only the stale ones")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any recording is missing or stale")
    args = parser.parse_args(argv)

    modules = sorted(CONTENT.glob("*.json"))
    if not modules:
        raise SystemExit("no built content; run python -m tools.build_content")

    problems = 0
    for path in modules:
        module = json.loads(path.read_text(encoding="utf-8"))
        folder = NARRATION / module["slug"]

        if args.check:
            behind = stale(module, load_manifest(folder), folder)
            if behind:
                print("%s: %d slide(s) not recorded from the current script: %s"
                      % (module["slug"], len(behind),
                         [l["ordinal"] for l in behind]))
                print("  those slides fall back to the browser's synthesiser.")
                print("  run: python -m tools.build_narration")
                problems += 1
            else:
                print("%s: every slide is recorded from the current script."
                      % module["slug"])
            continue

        backend = BACKENDS[args.backend](args.voice) if args.voice \
            else BACKENDS[args.backend]()
        record(module, backend, folder, args.force)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
