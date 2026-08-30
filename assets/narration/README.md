# Recorded narration

Drop the audio for a module in a folder named after it, then run the importer:

```
assets/narration/security-awareness-essentials/01.mp3
assets/narration/security-awareness-essentials/02.mp3
...
```

```bash
python -m tools.import_narration          # match, measure and record
python -m tools.import_narration --check  # report without writing
```

## Naming

A file is matched to a slide by the **first number in its name**, so all of
these find slide 3:

```
03.mp3        3.mp3        slide-03.mp3        03 - Phishing Emails.mp3
```

A file with no number is matched on the artwork name instead, so `phishing.mp3`
finds the slide whose image is `03-phishing.png`. Anything matching nothing, or
matching two slides, is reported rather than guessed at.

**Use mp3 or m4a.** Both play everywhere. Ogg and Opus are accepted and warned
about, because Safari will not play them and the learner would simply hear
nothing.

## Timings, if you have them

If a file named like the audio but ending `.vtt`, `.srt` or `.json` sits beside
it, the transcript will follow the recording word by word. Most text-to-speech
tools can export subtitles alongside the audio; that is all this needs.

Without one, the slide plays perfectly well and the transcript simply does not
highlight. A sidecar whose words do not match the script is reported and
ignored, rather than producing a highlight that drifts away from the voice.

## What the importer writes down

`manifest.json`, holding the SHA-256 of the words each file was imported
against. Importing is an assertion — a person saying "this is a recording of
this script" — and it is what makes staleness detectable afterwards.

**Edit the script without re-recording and the hash stops matching**, so the
server drops the recording and the browser reads that slide instead. A worse
voice, rather than a voice saying something the transcript on screen does not.
Re-run the importer once the new audio is in place.

## What is committed

Nothing in this folder except this file. The recordings are here on the machine
that imported them and inside any image built from it, but `.gitignore` still
excludes them: fifty-one minutes of mp3 is 48MB, and committing that to plain
git is a decision to take deliberately rather than by default. Git LFS is the
answer when it is taken.

Until then, **a fresh clone has no narration** and every slide falls back to
the browser voice. `python -m tools.import_narration --check` says so, and
exits non-zero, which makes it worth running before a deployment rather than
after one.
