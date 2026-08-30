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
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "assets" / "slides"
SCRIPT = ROOT / "data" / "source" / "security-awareness-voiceover-scripts.md"
QUESTIONS = ROOT / "data" / "source" / "knowledge-check.json"
OUTPUT = ROOT / "data" / "modules" / "security-awareness-essentials.json"

#: Slides that carry no narration, and why. Named so that a missing script is
#: a decision rather than an omission — the check below fails on any OTHER
#: unnarrated slide.
INTENTIONALLY_SILENT = {
    21: "the knowledge-check gate: a prompt to begin, not material to teach",
}

#: Pairings where the script title and the artwork slug legitimately share no
#: word, confirmed by looking at the slide. Named individually so the guard
#: below can still fail on a pairing NOBODY has checked — an allowlist of three
#: is a decision, a disabled check is not.
VERIFIED_PAIRINGS = {
    1:  "'Security Awareness Training' is the title slide, slug 'title'",
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
        # "## Slide 1 - Title: Security Awareness Training" carries a label
        # from the script's own structure. Without this it reaches the player
        # and the first thing every learner reads is "Title: ".
        title = re.sub(r"^(Title|Slide|Heading)\s*:\s*", "",
                       parts[i + 1].strip())
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


def opening(option: str) -> str:
    """The first three meaningful words of an option, for spotting the item
    where three distractors are phrased alike and the answer is not."""
    words = re.findall(r"[a-z]+", option.lower())
    return " ".join(words[:3])


def load_questions(slide_numbers: set) -> list:
    """The knowledge check, validated against the slides it claims to test.

    Every fault below is silent without this check, which is why they are
    worth code. An answer key pointing past the end of its own options grades
    everybody wrong for ever and reads as a question people find hard. A
    `teaches` naming a slide that does not exist breaks the report that says
    which material failed rather than which people did. And the last group are
    properties of the set rather than of any one question: if the answer is
    never the last option, or one position holds most of the answers, or the
    three wrong options are phrased alike and the right one is not, then the
    check can be scored without reading it -- and a score that means nothing
    is worse than no score, because it will be believed.
    """
    payload = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    questions, faults, seen = [], [], set()
    for q in payload.get("questions", []):
        n = q.get("ordinal")
        options = q.get("options") or []
        if len(options) < 2:
            faults.append("question %s has fewer than two options" % n)
        if len(set(options)) != len(options):
            faults.append("question %s repeats an option" % n)
        if (not isinstance(q.get("correct_index"), int)
                or not 0 <= q["correct_index"] < len(options)):
            faults.append(
                "question %s has correct_index %r, which is not one of its %d "
                "options - every answer would be graded wrong"
                % (n, q.get("correct_index"), len(options)))
        if q.get("teaches") not in slide_numbers:
            faults.append(
                "question %s says it teaches slide %r, which does not exist"
                % (n, q.get("teaches")))
        if q.get("prompt") in seen:
            faults.append("question %s repeats an earlier prompt" % n)
        seen.add(q.get("prompt"))
        if options and isinstance(q.get("correct_index"), int) \
                and 0 <= q["correct_index"] < len(options):
            # If every distractor opens with the same few words and the answer
            # does not, the answer is the odd one out and can be picked without
            # reading past the third word.
            openings = [opening(o) for o in options]
            answer = openings[q["correct_index"]]
            others = [o for i, o in enumerate(openings)
                      if i != q["correct_index"]]
            if len(others) > 1 and len(set(others)) == 1 and answer != others[0]:
                faults.append(
                    "question %s: every wrong option opens '%s' and the "
                    "correct one does not, so it is the odd one out"
                    % (n, others[0]))
        if not q.get("explains"):
            faults.append(
                "question %s has no explanation - a quiz that says only "
                "'incorrect' teaches nothing" % n)
        questions.append(q)

    # A whole-set property, checked only once every question is individually
    # sound. A learner who notices the answer is never the last option can
    # score without reading, and a position that is never correct is not a
    # distractor, it is decoration. Both make the check measure less than the
    # score suggests, which is the one failure this product cannot afford.
    if not faults:
        width = max((len(q["options"]) for q in questions), default=0)
        used = collections.Counter(q["correct_index"] for q in questions)
        if len(questions) >= width:
            unused = [i for i in range(width) if not used[i]]
            if unused:
                faults.append(
                    "option position(s) %s are never the correct answer across "
                    "%d questions, so 'never the last one' is a rule that can "
                    "be learned instead of the material"
                    % (unused, len(questions)))
            for position, count in sorted(used.items()):
                if count > len(questions) / 2:
                    faults.append(
                        "position %d is the answer to %d of %d questions - "
                        "guessing it beats knowing the material"
                        % (position, count, len(questions)))
    if faults:
        raise SystemExit("knowledge check is not usable:\n  "
                         + ("\n  ").join(faults))
    return questions


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

    questions = load_questions({l["ordinal"] for l in lessons})
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
        # Authored separately so the answers are not in the same diff as the
        # teaching material. They ARE in this built file, which is read on the
        # server - the API must strip `correct_index` and `explains` before a
        # question reaches a browser, or the quiz grades itself in the client.
        "questions": questions,
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
