"""Build the course content from the authored artwork and voice-over script.

The two halves of this course are authored separately and by different means:
the slides are images, the narration is prose in a markdown file. Nothing but
their numbering ties them together, and a mismatch would not look like an error
— it would look like a course, narrated slightly wrong, all the way through.

So this refuses to emit anything it cannot pair. Every image must have a slide
number, every narrated slide must have an image, and the titles must be
recognisably the same slide. A slide deliberately without narration (the quiz
prompt) has to be named here, so "no narration" is a decision on the record
rather than the absence of a file nobody noticed.

    python -m tools.build_content            # write data/modules/*.json
    python -m tools.build_content --check    # exit 1 if it is stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "assets" / "slides"
SCRIPT = ROOT / "data" / "source" / "security-awareness-voiceover-scripts.md"
OUTPUT = ROOT / "data" / "modules" / "security-awareness-essentials.json"

#: Slides that carry no narration, and why. Named so that a missing script is
#: a decision rather than an omission — the check below fails on any OTHER
#: unnarrated slide.
INTENTIONALLY_SILENT = {
    21: "the knowledge-check gate: a prompt to begin, not material to teach",
}

#: Pairings where the script title and the artwork slug legitimately share no
#: word, confirmed by looking at the slide. Named individually so the guard
#: below can still fail on a pairing NOBODY has checked — an allowlist of four
#: is a decision, a disabled check is not.
VERIFIED_PAIRINGS = {
    1:  "'Title: Security Awareness Training' is the title slide, slug 'title'",
    6:  "'Multi-Factor Authentication' is abbreviated to 'mfa' in the artwork",
    20: "'Key Takeaways' is the artwork's 'summary' slide — its image reads "
        "'Key Takeaways — You're Ready!'",
}

#: Delivery pace the script itself states, used to show a learner what they are
#: committing to before they press play.
WORDS_PER_MINUTE = 150


def parse_scripts() -> dict:
    """{slide number: (title, narration)} from the authored markdown."""
    md = SCRIPT.read_text(encoding="utf-8")
    parts = re.split(r"^## Slide (\d+)\s*[—-]\s*(.+)$", md, flags=re.M)[1:]
    out = {}
    for i in range(0, len(parts), 3):
        number = int(parts[i])
        title = parts[i + 1].strip()
        # Everything up to the horizontal rule that ends the section.
        body = parts[i + 2].split("\n---")[0].strip()
        out[number] = (title, body)
    return out


def parse_slides() -> dict:
    """{slide number: (slug, filename)} from the artwork."""
    out = {}
    for path in sorted(SLIDES.glob("*.png")):
        m = re.match(r"(\d+)-(.+)\.png$", path.name)
        if not m:
            raise SystemExit(
                "artwork %s is not named <nn>-<slug>.png, so it cannot be "
                "paired with a script" % path.name)
        out[int(m.group(1))] = (m.group(2), path.name)
    return out


def normalise(text: str) -> set:
    """Words that carry meaning in a slide title, for comparing two titles that
    were written by different hands: 'Vishing & Smishing' vs 'Vishing and
    Smishing' are the same slide."""
    stop = {"the", "a", "an", "and", "of", "in", "to", "is", "what", "your",
            "for", "on", "do", "title", "security"}
    words = re.findall(r"[a-z]+", text.lower())
    # Stem a trailing plural so "Password Best Practices" matches the artwork
    # slug "passwords". Crude on purpose: anything cleverer would need a
    # dependency, and the exceptions are named above rather than guessed at.
    return {w.rstrip("s") for w in words if w not in stop and len(w) > 2}


def build() -> dict:
    scripts, slides = parse_scripts(), parse_slides()

    missing_art = sorted(set(scripts) - set(slides))
    if missing_art:
        raise SystemExit(
            "narration exists for slide(s) %s with no artwork. Refusing to "
            "build: the deck would be narrated out of step from that point on."
            % missing_art)

    unexplained = sorted(set(slides) - set(scripts) - set(INTENTIONALLY_SILENT))
    if unexplained:
        raise SystemExit(
            "slide(s) %s have artwork and no narration, and are not listed in "
            "INTENTIONALLY_SILENT. Add the script, or record why the slide is "
            "silent — an unnarrated slide should be a decision, not an "
            "omission nobody noticed." % unexplained)

    lessons, mismatched = [], []
    for number in sorted(slides):
        slug, filename = slides[number]
        title, narration = scripts.get(number, ("", ""))
        if narration:
            # The titles were written separately; if they share no meaningful
            # word the pairing is probably wrong even though the numbers line up.
            paired = normalise(title) & normalise(slug)
            if not paired and number not in VERIFIED_PAIRINGS:
                mismatched.append((number, title, slug))
        words = len(narration.split())
        lessons.append({
            "ordinal": number,
            "title": title or slug.replace("-", " ").title(),
            "image": "slides/" + filename,
            "narration": narration,
            "narration_seconds": round(words / WORDS_PER_MINUTE * 60) if words else 0,
            "silent_because": INTENTIONALLY_SILENT.get(number, ""),
        })

    if mismatched:
        raise SystemExit(
            "the script title and the artwork name share no word on slide(s) "
            "%s. That usually means the deck and the script disagree about "
            "what slide this is, which is silent when it happens and wrong "
            "for every slide after it."
            % [(n, t, s) for n, t, s in mismatched])

    total = sum(l["narration_seconds"] for l in lessons)
    return {
        "slug": "security-awareness-essentials",
        "title": "Security Awareness Essentials",
        "summary": ("How attackers actually reach people, what those attempts "
                    "look like, and the habits that stop them. No jargon, no "
                    "technical background needed."),
        "topic": "general",
        "minutes": round(total / 60),
        "narration_seconds": total,
        "lessons": lessons,
        # Authored separately; the knowledge check lives in its own file so the
        # answers are not sitting in the same diff as the teaching material.
        "questions": [],
    }


def rendered() -> str:
    payload = build()
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    payload["content_hash"] = digest
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed content is stale")
    args = ap.parse_args(argv)

    text = rendered()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current.replace("\r\n", "\n") == text:
            print("course content is up to date.")
            return 0
        print("course content IS STALE. Run: python -m tools.build_content")
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    payload = json.loads(text)
    print("wrote %s" % OUTPUT.relative_to(ROOT))
    print("  %d slides, %d narrated, %d minutes of narration"
          % (len(payload["lessons"]),
             sum(1 for l in payload["lessons"] if l["narration"]),
             payload["minutes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
