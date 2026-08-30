# Security Awareness Portal

A narrated security awareness course that measures whether people learned
something, rather than whether they reached the last page.

Twenty-one slides with voice-over and a twelve-question knowledge check.
Sign-in is through Microsoft Entra ID.

---

## The thing this is built around

Awareness training is bought and reported on completion: *94% of staff are
trained*. Completion measures that somebody reached the last page. It does not
measure that they can spot a phish, and a portal that reports the first as the
second tells a CISO their organisation is safe on evidence that does not
support it.

So completion and learning are kept apart here, and never collapsed into one
number:

| | what it records |
|---|---|
| `enrolment` | did this person open it, and how far did they get |
| `attempt` / `response` | did they answer correctly, on which try, and how fast |
| `question_stat` | does this question tell anybody apart, or does everyone get it right |

That last row is the portal reporting on itself. **A question that everybody
answers correctly on the first attempt measures nothing** — it cannot separate
a person who understands from one who does not, so a high score on it is not
evidence of awareness. `question_stat` exists to find those questions so they
can be replaced.

Every question also names the slide it tests, so a low correct rate points at
the material that failed rather than at the people who answered.

---

## Running it

```bash
docker compose up -d                 # Postgres on 5434
cp .env.example .env                 # then edit
pip install -r requirements.txt
python -m tools.build_content        # slides + script -> data/modules/*.json

# a session, without needing Entra configured
python -m server.devsession --email you@example.com --name "Your Name"

uvicorn server.api:app --reload      # http://localhost:8000
```

For the front end in development:

```bash
cd frontend && npm install && npm run dev     # http://localhost:5174
```

`npm run build` emits into `server/spa/`, which the API serves when present.

### Tests

```bash
python -m pytest                # 82, needs the database from docker compose
cd frontend && npm test         # 26
```

---

## How it is put together

```
assets/slides/       the artwork, 21 PNGs
data/source/         the voice-over script, and the knowledge check
data/modules/        built content (generated — do not hand-edit)
tools/build_content  pairs artwork with script; refuses to emit a mismatch
server/              FastAPI, Postgres, Entra sign-in
frontend/            React + Vite; the player and the check
```

Content is authored as files and the database is downstream of them. The load
runs on start and is idempotent.

### The narration

Spoken by the browser, not shipped as audio files: nothing to host, nothing to
keep in step with a script that gets edited, and the words stay reviewable in a
diff. `lesson.audio_url` is the escape hatch — when it is set the player
prefers it, so swapping in a recorded voice is a content change rather than a
code change.

Browser speech synthesis has four failure modes that look like nothing at all,
and all four are handled in [`frontend/src/narration.ts`](frontend/src/narration.ts):
voices that load asynchronously, Chrome stopping after fifteen seconds,
`cancel()` firing `end` in some browsers and not others, and speech needing a
user gesture.

On a machine with no voices installed the player **says so** and shows the
script. The transcript is on screen throughout regardless, and the word being
spoken is highlighted — which is also what makes the course usable without
hearing it.

---

## What the code refuses to do

Most of the tests are about refusals, because the failures that matter here are
the ones that still look like a working portal.

**The answers do not leave the server.** Questions cross to the browser through
one function that rebuilds each one from an allowlist of three fields. An
allowlist rather than "everything except the answer", because the second is one
new field away from shipping the answers and nothing looks wrong when it does:
the quiz still works, it is just answerable by anyone who opens the network tab.

**A question is answered once per attempt.** Re-answering until the tick appears
turns a knowledge check into a clicking exercise and produces a score that
cannot be told apart from knowing the material. A retake is a new numbered
attempt, and the number is reported: a pass on the third go is not the same
evidence as a pass on the first.

**Unanswered questions count as wrong.** Otherwise skipping what you do not know
scores better than attempting it.

**Content that cannot be paired is not built.** Every slide must have a script
and every script a slide; a slide deliberately without narration has to be
named, with a reason, so silence is a decision rather than an omission nobody
noticed. The knowledge check is refused if an answer key points past the end of
its options, if a question names a slide that does not exist, if one option
position holds most of the answers, or if the three wrong options are phrased
alike and the right one is not — all of which let somebody score without
reading.

**Editing content does not rewrite history.** Each response stores the question
as it was asked, and a question that is no longer authored is retired rather
than deleted — `response` references `question` with `ON DELETE CASCADE`, so
deleting one would destroy the answers that are the whole point.

**Sign-in is scoped to your tenant.** The authority is the tenant id, never
`common`, and the `tid` claim is checked as well. People are matched on the
immutable Entra `oid` and never on email, which changes on marriage, transfer
or a domain rebrand — matching on it would hand somebody a second, empty
training record on the day it happened.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
