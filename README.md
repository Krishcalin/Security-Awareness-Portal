# Security Awareness Portal

A narrated security awareness course that measures whether people learned
something, rather than whether they reached the last page.

Twenty-one slides with voice-over and a twelve-question knowledge check.
Sign-in is through Microsoft Entra ID. Pass at 70% and a certificate is issued
in your name and emailed from the CISO's office. Leave halfway through and you
come back to the slide you left.

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

The whole thing, in Docker:

```bash
docker compose up -d --build

docker compose exec app python -m server.devsession \
    --email you@example.com --first Your --last Name --link
```

Open the link it prints.

| | |
|---|---|
| http://localhost:8080 | the portal |
| http://localhost:8081 | Mailpit — every certificate email is caught here rather than posted to anybody |
| localhost:5434 | Postgres |

The compose file is **for development and says so**: the session secret is in
plain sight, cookies are not Secure because this is http, and `ALLOW_DEV_SIGNIN`
is on. None of those belong anywhere but a laptop.

`devsession` exists because there is no login endpoint. `/auth/dev` redeems a
token already signed with `SESSION_SECRET` — anybody who can produce one could
set the cookie directly, so it grants nothing new — and it is gated twice
anyway: off unless `ALLOW_DEV_SIGNIN=1`, and it 404s the moment `ENTRA_*` is
configured, so it can never sit beside real sign-in.

### Without Docker

```bash
docker compose up -d db              # Postgres on 5434
cp .env.example .env                 # then edit
pip install -r requirements.txt
python -m tools.build_content        # slides + script -> data/modules/*.json
python -m server.devsession --email you@example.com --first Your --last Name
uvicorn server.api:app --reload      # http://localhost:8000
```

For the front end in development:

```bash
cd frontend && npm install && npm run dev     # http://localhost:5174
```

`npm run build` emits into `server/spa/`, which the API serves when present.
The image builds it in a separate stage, so Node and 200 build-time packages
never reach the thing that runs.

### Tests

```bash
python -m pytest                # 186, needs the database from docker compose
cd frontend && npm test         # 74
```

---

## How it is put together

```
assets/slides/       the artwork, 21 PNGs
assets/certificate/  the certificate artwork (PNG master + derived JPEG)
assets/brand/        the logo (PNG master + derived lockup, mark, favicon)
assets/narration/    recorded narration, and where each word falls in it
data/source/         the voice-over script, and the knowledge check
data/modules/        built content (generated — do not hand-edit)
tools/build_content  pairs artwork with script; refuses to emit a mismatch
server/              FastAPI, Postgres, Entra sign-in
server/templates/    the sign-in page, the one screen the SPA cannot render
frontend/            React + Vite; the player and the check
```

Content is authored as files and the database is downstream of them. The load
runs on start and is idempotent.

### The narration

**Recorded once and served**, rather than read aloud by whatever voice the
learner's machine happens to have. Browser speech synthesis was the original
choice and it was the wrong one: it sounds like whatever is installed — on a
stock Windows box, two legacy American voices reading British spelling — it
clips the opening of every utterance on Chrome for Windows, and it means no
two employees hear the same course, which is a strange property for something
that issues a certificate.

Recordings are **supplied**, not generated here. Drop the audio for a module
into `assets/narration/<module-slug>/` and run the importer:

```bash
python -m tools.import_narration          # match, measure and record
python -m tools.import_narration --check  # report without writing
```

A file is matched to a slide by the first number in its name, so `03.mp3`,
`slide-03.mp3` and `03 - Phishing Emails.mp3` all find slide 3; a file with no
number is matched on the artwork name instead. Anything that matches nothing,
or two slides, is **reported rather than guessed at** — the wrong slide's audio
is worse than none, and nothing about it looks wrong from the outside. See
[`assets/narration/README.md`](assets/narration/README.md).

`tools/build_narration.py` can also synthesise a set, which was how the
pipeline was proved before any real audio existed. It refuses to run over
supplied recordings without `--force`, because replacing a real voice with a
machine one is the sort of thing only discovered by listening to the whole
course again.

Which recordings a deployment holds is **not part of the course**: the same
content JSON is loaded by a server that has the audio and by one that does
not. So recordings are resolved when content is loaded rather than baked into
the built file, which would otherwise differ depending on which machine ran
the build.

**The risk this has to close is staleness.** Audio files go out of date: the
script is edited, the recording is not, and a learner hears one wording while
reading another on screen. That is worse than a robotic voice — it is wrong,
and it is invisible to whoever made the edit. So every recording stores the
SHA-256 of the exact words it was made from, and the content build attaches a
recording to a slide **only while that hash still matches**. Drift cannot
produce wrong audio; it can only fall back to the synthesiser, which is a bad
voice rather than a lie. Whitespace is normalised first, so re-wrapping a
paragraph does not throw away a perfectly good take.

**Timings are optional, and checked before they are believed.** If a `.vtt`,
`.srt` or `.json` file sits beside the audio — most text-to-speech tools can
export subtitles — the transcript follows the recording word by word. Words
inside a cue are spread across it by length, which is an approximation, but a
bounded one: every cue re-synchronises, so it cannot drift. Spreading words
across a whole minute-long slide would, which is why that is never done.

A sidecar whose words are not in the script is refused, and so is one that runs
past the end of its own audio. Measured on the Windows synthesiser, its own
word report was wrong in two independent ways over a minute of speech — the
timeline ran about a third long, and the character offsets drifted twenty-one
characters by the end, so a mark that should point at "you" pointed into the
middle of "that works". A track that is approximately right is worse than none,
because the highlight creeps ahead of the voice until the learner distrusts the
reading rather than the highlight.

**Falling back, never falling silent.** A slide with no current recording is
read by the browser. So is one whose file fails to load. The transcript is on
screen throughout either way, which is also what makes the course usable
without hearing it at all — and on a machine with no voices installed *and* no
recording, the player says so rather than being silently silent.

Browser speech synthesis, still the fallback, has four failure modes that look
like nothing at all, and all four are handled in
[`frontend/src/narration.ts`](frontend/src/narration.ts):
voices that load asynchronously, Chrome stopping after fifteen seconds,
`cancel()` firing `end` in some browsers and not others, and speech needing a
user gesture.

**The narration is spoken a sentence at a time**, with real silence between.
Handed a whole slide, the engines run sentences together: a full stop gets no
more silence than a comma and the first word of the next sentence lands before
the listener has finished the last one, so it is heard as a stumble or not
heard at all. Nothing in the markup fixes this — SSML `<break>` is ignored by
the Web Speech API, and padding with spaces or commas either does nothing or
gets read aloud. The only thing that reliably produces silence is silence.

So the text is split on its own punctuation and each piece spoken as its own
utterance: 350ms after a full stop, 200ms after a colon, 650ms between
paragraphs.

**Each utterance is led by a comma**, which exists to be thrown away. Chrome on
Windows clips the opening of an utterance — the audio device is still starting
when the synthesiser begins — so the first word goes missing. One utterance per
slide costs the first word of the slide; one per sentence costs the first word
of every sentence, which is the same complaint in a new place. The comma gives
the clip something to eat, is not read aloud, and is subtracted back out of the
word-boundary offsets so the transcript is unaffected.

Clipping is a property of the speech engine, and it cannot be verified by
reading code. [`tools/narration-check.html`](tools/narration-check.html) opens
straight from the filesystem and plays the same passage seven ways — one
utterance, split with and without a lead-in, longer and shorter gaps, queued
back to back — against any installed voice, so the question is settled by
listening rather than by argument. A test asserts its variant C is exactly
what the portal ships. Those pauses are part of how long a slide takes, so the build
counts them — the course went from a reported nineteen minutes to a truthful
twenty-one the moment they were added.

The splitter is deliberately simple, because the script is: British prose with
no abbreviations, no decimals and no ellipses. A test fails if any of those
ever appear, rather than letting "e.g." quietly become two sentences with a
pause in the middle. Another runs the splitter over all twenty slides and
checks the pieces add back up to exactly the script.

On a machine with no voices installed the player **says so** and shows the
script. The transcript is on screen throughout regardless, and the word being
spoken is highlighted — which is also what makes the course usable without
hearing it.


---

## Certificates

Pass the knowledge check at **70%** and a certificate is issued, downloadable
immediately, and emailed from the CISO's address with the PDF attached.

It is the supplied artwork with two things drawn on it — the name and the date.
Both positions are measured from the artwork rather than guessed, and a test
fails if a redraw moves the rules out from under them.

**The name is the given and family names from Entra**, as separate claims.
Entra's `name` is a *display* name, which in plenty of tenants is
"De, Krishnendu (Security)" — splitting that on a space is how the wrong thing
ends up printed on somebody's certificate. If a tenant does not populate the
name claims, it falls back to the display name and then to the local part of
the email address, because a blank line where a name should be is only noticed
by the person who receives it. A name too long for the rule is set smaller,
never truncated.

**The name is frozen at issue.** A certificate is a statement about a moment:
somebody changing their surname next year does not retrospectively alter the
document they were already sent, and re-rendering it for download cannot
produce something different from what went out by email.

**A failure to send is recorded, not swallowed.** An email that bounced, one
that was never attempted because SMTP is not configured, and one that arrived
look identical from inside an application unless the difference is written
down — so `certificate.emailed_at` and `certificate.email_error` are that
difference. The screen says "on its way to <address>" only when something is
actually being posted, and the certificate is downloadable either way. Mail is
a copy, never the delivery mechanism.

> **Deployment note.** Sending as an address at a domain whose mail this server
> does not handle will be rejected or filed as spam, and being the CISO's
> address makes that *more* likely rather than less — those domains tend to
> have a strict DMARC policy, which is the point of having one. This host must
> be an authorised sender in the domain's SPF record, and the message ought to
> be DKIM-signed. Leave `SMTP_HOST` unset and nothing is sent at all.

## Picking up where you left off

Progress is written on every slide change, and two numbers are kept because
they answer different questions:

| | |
|---|---|
| `furthest_ordinal` | how far they got — a high-water mark, never goes down |
| `last_ordinal` | where they actually were when they stopped |

Somebody who reaches slide 18, goes back to slide 4 to re-read it and closes
the tab **left at 4**, and that is where they resume — while their progress is
still 18. Signing in takes them straight there rather than to the front page,
and a deep link they followed still wins over it.

An unfinished set of questions beats a slide: somebody who stopped halfway
through the check is further on than the last slide they looked at, and
dropping them back into the deck would strand the answers they had already
given, which cannot be given again in the same attempt.


---

## The sign-in page and the brand

`/auth/login` is a server-rendered page rather than a route in the app,
because it is the one screen somebody sees while they have no session — the
app cannot render it without one. It does **not** bounce to Microsoft on its
own: an automatic redirect gives nobody a chance to see whose portal this is,
and "I followed a link and ended up on a Microsoft password box" is the shape
of the thing this course spends twenty minutes warning people about. The
button goes to `/auth/start`, which is what talks to Entra.

Form on the left, brand on the right — and **the brand panel is second in the
document**. Left and right are CSS; document order is what a keyboard and a
screen reader follow, so what somebody came to do comes first. Below 900px the
brand moves *above* the form, because a bare sign-in button with no branding
over it is exactly what a phishing page looks like.

The supplied logo is the master and stays untouched.
`tools/build_brand_assets.py` derives everything the site serves — the trimmed
lockup, a transparent version, the shield on its own, and a favicon — so a
redraw has one place to reach and no hand-cropped copy drifts from it.

Two things the master cannot be used for as it stands, both handled there:

- **It carries its own margin.** The lockup sits in about a third of a
  2400x1792 page of white, and a layout cannot space something that brings its
  own whitespace. The content is trimmed to its own bounds and the margin put
  back in CSS.
- **It is opaque white** — fine on a light panel, a visible white sticker on a
  dark one. Transparency is recovered from the white ground, which works only
  because the artwork was drawn on white.

**The brand panel stays light in both themes.** The lockup is drawn for a light
ground: on a dark one the navy "Be Aware." and the grey strapline all but
disappear, so a dark panel would hide half the wordmark to honour a preference
nobody expressed about a logo. The shield alone survives either ground, so
that is what the app's own header uses.

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

**A certificate is never issued by the browser.** The pass mark lives on the
server and nowhere else. A threshold the client also holds is one the two can
disagree about, and the disagreement shows up as a certificate the server never
awarded. Somebody else's certificate is a 404, because the serial is quotable
and must not also be the key to the PDF.

**Sign-in is scoped to your tenant.** The authority is the tenant id, never
`common`, and the `tid` claim is checked as well. People are matched on the
immutable Entra `oid` and never on email, which changes on marriage, transfer
or a domain rebrand — matching on it would hand somebody a second, empty
training record on the day it happened.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
