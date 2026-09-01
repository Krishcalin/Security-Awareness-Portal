import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router"
import {
  ChevronLeft, ChevronRight, Captions, Pause, Play, Volume2, VolumeX,
} from "lucide-react"

import { api } from "../api/client"
import { dueDate } from "../format"
import type { CheckpointState, Lesson, ModuleDetail } from "../api/types"
import { Checkpoint, checkpointAfter } from "../components/Checkpoint"
import { Narrator, type NarratorStatus } from "../narration"
import { Completed } from "./Completed"

/** Seconds as a listener reads them. */
function clock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`
}

/** One word, and when it is said. Produced by tools/build_narration.py. */
interface Mark { ms: number; at: number; len: number }

/** Assets are served from /media unless the content gives a full URL, which
 *  is how a recording could later be moved to a CDN without a code change. */
function mediaUrl(path: string): string {
  return /^https?:\/\//.test(path) ? path : "/media/" + path
}

/** The last word started at or before `ms`. */
function spokenIndex(marks: Mark[], ms: number): number {
  let low = 0, high = marks.length - 1, found = -1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (marks[middle].ms <= ms) { found = middle; low = middle + 1 }
    else high = middle - 1
  }
  return found
}

/**
 * Whether to show the words on screen.
 *
 * OFF by default: the course is meant to be listened to, and a wall of text
 * beside the voice invites people to read ahead instead of hearing it.
 *
 * Not removed, though. The transcript is the only way somebody who is deaf or
 * hard of hearing can take this course, and a compliance course that part of
 * the workforce cannot take is a worse problem than a cluttered screen. It is
 * also what a machine with no voices and no recording falls back to. So it is
 * one control away, the choice is remembered, and it is forced on when there
 * is genuinely nothing to hear.
 */
const TRANSCRIPT_PREFERENCE = "show-transcript"

function wantsTranscript(): boolean {
  try {
    return localStorage.getItem(TRANSCRIPT_PREFERENCE) === "yes"
  } catch {
    // Storage blocked. Default to the quiet screen; the control still works
    // for this visit.
    return false
  }
}

/** Words with their offsets, for following the narration as it is spoken. */
function words(text: string): { text: string; start: number }[] {
  const found: { text: string; start: number }[] = []
  const pattern = /\S+/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    found.push({ text: match[0], start: match.index })
  }
  return found
}

function Transcript({ narration, spokenAt }: {
  narration: string
  spokenAt: number
}) {
  const parts = useMemo(() => words(narration), [narration])
  const current = useMemo(() => {
    if (spokenAt < 0) return -1
    for (let i = parts.length - 1; i >= 0; i--) {
      if (parts[i].start <= spokenAt) return i
    }
    return -1
  }, [parts, spokenAt])

  return (
    <p className="font-[family-name:var(--font-read)] text-lg leading-relaxed">
      {parts.map((word, i) => (
        <span key={i} className={i === current ? "spoken-now" : undefined}>
          {word.text}{i < parts.length - 1 ? " " : ""}
        </span>
      ))}
    </p>
  )
}

export function Player() {
  const { slug = "" } = useParams()
  const [search] = useSearchParams()
  const navigate = useNavigate()

  const [detail, setDetail] = useState<ModuleDetail | null>(null)
  const [problem, setProblem] = useState("")
  const [index, setIndex] = useState(0)
  const [status, setStatus] = useState<NarratorStatus>("idle")
  const [spokenAt, setSpokenAt] = useState(-1)
  /** How far through this slide's narration, 0 to 1, and the seconds behind
   *  it. Recorded audio and the browser synthesiser report position in
   *  completely different ways; `advance` below is where that is reconciled. */
  const [heard, setHeard] = useState({ fraction: 0, seconds: 0, total: 0 })
  const [muted, setMuted] = useState(false)
  const [autoAdvance, setAutoAdvance] = useState(true)
  const [reachedEnd, setReachedEnd] = useState(false)
  /**
   * The slides whose narration has run to the end in this sitting.
   *
   * The forward button is off until the slide ON SCREEN is in here. Not a
   * high-water mark of how far somebody got: progress is recorded on arrival
   * at a slide, so a mark like that says where they have BEEN, and being
   * somewhere is exactly what this is meant to stop counting.
   *
   * A set rather than a number so that going back does not cost a second
   * listen. Somebody who has heard one to six can step back to three and walk
   * forward again without sitting through any of it twice — every slide on the
   * way is one they have already heard. A slide they skipped past is not.
   *
   * Empty at the start of each sitting, including a resume. Coming back to a
   * course tomorrow and continuing from the slide you stopped on means
   * hearing that slide, which is the one you were in the middle of.
   */
  const [heardSlides, setHeardSlides] = useState<Set<number>>(new Set())

  /* THE CHECKPOINT BETWEEN THIS SLIDE AND THE NEXT.
   *
   * `heardSlides` proves the audio reached the end; it cannot prove anybody
   * was listening to it, and a course left playing to an empty chair reaches
   * the last slide exactly as one that was watched. Two questions every fifth
   * slide are the smallest honest test that somebody is still there.
   *
   * Held as state rather than derived, because whether it has been answered is
   * the server's answer and not this component's: a learner who answered on
   * their phone this morning does not do it again this afternoon. */
  const [checkpoint, setCheckpoint] = useState<CheckpointState | null>(null)
  const [showTranscript, setShowTranscript] = useState(wantsTranscript)

  const narrator = useRef(new Narrator())
  const audio = useRef<HTMLAudioElement | null>(null)
  const marks = useRef<Mark[]>([])
  const following = useRef<number | null>(null)
  /** Wall clock, for the synthesiser only: it has no position to ask for. */
  const spokenMs = useRef(0)
  const lastTick = useRef(0)
  // Read inside callbacks that outlive the render they were made in.
  const autoAdvanceNow = useRef(autoAdvance)
  autoAdvanceNow.current = autoAdvance

  /* DOES THE COURSE HAVE TO STOP ON THIS SLIDE?
   *
   * THE BUG THIS FIXES. The forward gate lives in `step`, and auto-advance
   * does not go through `step` — when a recording ends it calls `goTo` itself,
   * from three places. Auto-advance is ON by default, so slide five finished,
   * the checkpoint was fetched, and the player moved to slide six before
   * anybody could see it. Reported from a real run: "I am at slide 8 and I
   * have still not seen the questions."
   *
   * A ref rather than the state itself, because the three readers are inside
   * callbacks handed to an <audio> element and a speech synthesiser, which
   * close over the render that created them.
   *
   * `checkpoint` is still null at the instant a recording ends — the fetch is
   * only triggered by that same event — so this asks whether the slide HAS a
   * checkpoint, not whether one has arrived. Erring towards stopping: pausing
   * a course that turns out to have nothing to ask costs one click, and not
   * pausing loses the feature. */
  const stopHereNow = useRef(false)
  const lastIndex = (detail?.lessons.length ?? 1) - 1
  const indexNow = useRef(index)
  indexNow.current = index
  const detailNow = useRef<ModuleDetail | null>(null)
  detailNow.current = detail

  const lesson: Lesson | undefined = detail?.lessons[index]

  // Voices load asynchronously and do not need a gesture, so this happens on
  // mount. Only `speak()` needs the click.
  useEffect(() => {
    narrator.current.prepare().then((ok) => {
      if (!ok) setStatus("unsupported")
    })
    const current = narrator.current
    return () => current.stop()
  }, [])

  useEffect(() => {
    api.module(slug).then((loaded) => {
      setDetail(loaded)
      const asked = Number(search.get("slide"))
      const resume = Number.isFinite(asked) && asked > 0
        ? loaded.lessons.findIndex((l) => l.ordinal === asked) : -1
      setIndex(resume >= 0 ? resume : 0)
    }).catch((error) => setProblem(String(error.message)))
  }, [slug, search])

  // The word track for the slide on screen. Small, and only for the slide
  // being looked at — the whole course would be a megabyte of timings.
  useEffect(() => {
    marks.current = []
    if (!lesson?.audio_timings_url) return
    let stale = false
    fetch(mediaUrl(lesson.audio_timings_url))
      .then((response) => response.ok ? response.json() : [])
      .then((loaded: Mark[]) => { if (!stale) marks.current = loaded })
      .catch(() => {
        // No track: the recording still plays, the transcript simply does
        // not follow it. Losing the highlight is not worth an error.
      })
    return () => { stale = true }
  }, [lesson])

  /**
   * One frame of playback: the transcript position and the progress bar.
   *
   * The two sources of truth are not alike. A recording knows exactly where it
   * is and how long it runs, so the bar is exact. The browser synthesiser
   * exposes no position at all — so time spoken is accumulated here and
   * measured against the slide's estimated length, which is an estimate
   * running against an estimate. It is honest enough for a bar and would not
   * be honest enough for a number, which is why the seconds shown next to it
   * are the recording's own when there is one.
   */
  const advance = useCallback(() => {
    const element = audio.current
    const now = performance.now()

    if (element && !element.paused && !element.ended) {
      const total = Number.isFinite(element.duration) ? element.duration : 0
      setHeard({
        fraction: total > 0 ? element.currentTime / total : 0,
        seconds: element.currentTime,
        total,
      })
      const at = spokenIndex(marks.current, element.currentTime * 1000)
      setSpokenAt(at >= 0 ? marks.current[at].at : -1)
    } else if (speaking.current) {
      spokenMs.current += now - lastTick.current
      const total = estimated.current
      const seconds = spokenMs.current / 1000
      setHeard({
        fraction: total > 0 ? Math.min(1, seconds / total) : 0,
        seconds: Math.min(seconds, total),
        total,
      })
    } else {
      // Nothing is speaking any more, so the loop stops here. Releasing the
      // handle matters: `startFollowing` will not schedule anything while one
      // is held, so a loop that ended without clearing it can never be
      // restarted, and the bar stays at nothing for the rest of the course.
      following.current = null
      return
    }
    lastTick.current = now
    following.current = requestAnimationFrame(advance)
  }, [])

  /** Whether the SYNTHESISER is mid-sentence; the audio element speaks for
   *  itself. Held in a ref because the loop above outlives the render. */
  const speaking = useRef(false)
  const estimated = useRef(0)

  const startFollowing = useCallback(() => {
    lastTick.current = performance.now()
    if (following.current === null) following.current = requestAnimationFrame(advance)
  }, [advance])

  const followRecording = startFollowing

  /** This slide has been heard to the end, so the course can move on. */
  const heardToTheEnd = useCallback(() => {
    const lesson = detailNow.current?.lessons[indexNow.current]
    if (!lesson) return
    setHeardSlides((was) => new Set(was).add(lesson.ordinal))
  }, [])

  const stopFollowing = useCallback(() => {
    speaking.current = false
    if (following.current !== null) {
      cancelAnimationFrame(following.current)
      following.current = null
    }
  }, [])

  useEffect(() => stopFollowing, [stopFollowing])

  useEffect(() => {
    try {
      localStorage.setItem(TRANSCRIPT_PREFERENCE, showTranscript ? "yes" : "no")
    } catch {
      // Nothing to do; the choice simply lasts for this visit.
    }
  }, [showTranscript])

  useEffect(() => {
    // Nothing to listen to here, so there is nothing to wait for. A gate that
    // can never open is not a gate, it is somebody stuck on slide nine with
    // no way on and no way to tell why.
    if (lesson && (!lesson.narration ||
                   (status === "unsupported" && !lesson.audio_url))) {
      setHeardSlides((was) => was.has(lesson.ordinal)
        ? was : new Set(was).add(lesson.ordinal))
    }
  }, [lesson, index, status])

  useEffect(() => {
    if (lesson) void api.recordProgress(slug, lesson.ordinal).catch(() => {
      // Progress is a convenience, not the record of record. Losing one
      // update must not interrupt somebody in the middle of the course.
    })
  }, [slug, lesson])

  const goTo = useCallback((next: number) => {
    narrator.current.stop()
    stopFollowing()
    if (audio.current) {
      audio.current.pause()
      audio.current.currentTime = 0
    }
    setStatus("idle")
    setSpokenAt(-1)
    spokenMs.current = 0
    setHeard({ fraction: 0, seconds: 0, total: 0 })
    setIndex(next)
  }, [stopFollowing])

  const speakLesson = useCallback((at: number) => {
    const target = detail?.lessons[at]
    if (!target) return
    setSpokenAt(-1)
    if (target.audio_url) {
      // A recording wins whenever there is one. It sounds the same for every
      // learner, it does not depend on which voices a laptop happens to have
      // installed, and it does not clip the first word of a sentence the way
      // the browser synthesiser does on Chrome for Windows.
      //
      // There is no recording when the script has been edited since the last
      // one was made, and then this falls through to the synthesiser below —
      // a worse voice, rather than a voice saying something the transcript
      // does not.
      audio.current?.play().then(() => {
        setStatus("speaking")
        followRecording()
      }).catch(() => setStatus("idle"))
      return
    }
    // Nothing reports position, so the bar is driven from time spoken here
    // against the slide's estimated length.
    speaking.current = true
    estimated.current = target.narration_seconds
    spokenMs.current = 0
    startFollowing()
    narrator.current.speak(target.narration, {
      onStatus: (next) => {
        speaking.current = next === "speaking"
        setStatus(next)
      },
      onWord: setSpokenAt,
      onEnd: () => {
        speaking.current = false
        setHeard((was) => ({ ...was, fraction: 1, seconds: was.total }))
        heardToTheEnd()
        if (!autoAdvanceNow.current || stopHereNow.current) return
        const next = indexNow.current + 1
        if (next <= lastIndex) goTo(next)
        else setReachedEnd(true)
      },
    })
  }, [detail, goTo, heardToTheEnd, lastIndex, followRecording,
      startFollowing])

  const playing = status === "speaking"

  const togglePlay = useCallback(() => {
    if (status === "unsupported") return
    if (playing) {
      narrator.current.pause()
      audio.current?.pause()
      stopFollowing()
      setStatus("paused")
    } else if (status === "paused") {
      if (lesson?.audio_url) {
        void audio.current?.play().then(startFollowing)
        setStatus("speaking")
      } else {
        narrator.current.resume()
        speaking.current = true
        startFollowing()
      }
    } else {
      speakLesson(index)
    }
  }, [index, lesson, playing, speakLesson, startFollowing, status,
      stopFollowing])

  // Advancing while speaking should carry the voice to the new slide rather
  // than leaving the previous one talking over it.
  const step = useCallback((delta: number) => {
    const next = Math.min(Math.max(index + delta, 0), lastIndex)
    if (next === index) return
    // Forwards is what has to be earned. Checked here rather than only on the
    // button, so the arrow keys obey the same rule.
    if (delta > 0 && !heardSlides.has(detail?.lessons[index]?.ordinal ?? -1)) {
      return
    }
    // The arrow keys obey the checkpoint too. Checked here as well as on the
    // button because this is the path a keyboard takes, and a gate that only
    // exists on a button is a gate with a keyboard shortcut around it.
    if (delta > 0 && checkpoint && !checkpoint.complete) return
    const wasPlaying = playing
    goTo(next)
    if (wasPlaying) window.setTimeout(() => speakLesson(next), 60)
  }, [checkpoint, detail, goTo, heardSlides, index, lastIndex, playing,
      speakLesson])

  /* Fetched only once the slide has actually been heard: asking for it on
   * arrival would put two questions about material somebody is still
   * listening to on the screen underneath them. */
  const ordinal = lesson?.ordinal ?? 0
  const stop = detail ? checkpointAfter(ordinal, detail.lessons.length) : null
  const heardThis = heardSlides.has(ordinal)
  stopHereNow.current = stop !== null && !(checkpoint?.complete ?? false)
  useEffect(() => {
    if (stop === null || !heardThis) { setCheckpoint(null); return }
    let cancelled = false
    api.checkpoint(slug, stop)
      .then((state) => { if (!cancelled) setCheckpoint(state) })
      // A checkpoint that cannot be fetched must not become a locked door:
      // the course is the thing somebody came for, and a network failure
      // between two slides is not a reason to end their training.
      .catch(() => { if (!cancelled) setCheckpoint(null) })
    return () => { cancelled = true }
  }, [slug, stop, heardThis])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement &&
          ["INPUT", "TEXTAREA", "BUTTON"].includes(event.target.tagName)) return
      if (event.key === "ArrowRight") step(1)
      else if (event.key === "ArrowLeft") step(-1)
      else if (event.key === " ") { event.preventDefault(); togglePlay() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [step, togglePlay])

  if (problem) {
    return <p className="mx-auto max-w-4xl px-5 py-10 text-wrong">{problem}</p>
  }
  // Before the loading check below, because a completed module arrives with no
  // lessons — the server stops serving them — and `!lesson` would otherwise
  // leave somebody who has passed staring at "Loading…" for ever.
  if (detail?.completed && !detail.reviewing) {
    return <Completed title={detail.title} completed={detail.completed}
                      cycle={detail.cycle} />
  }
  if (!detail || !lesson) {
    return <p className="mx-auto max-w-4xl px-5 py-10 text-muted">Loading…</p>
  }

  // No recording and no voice on this machine: the words are the only way
  // this slide says anything, so they are not optional here.
  const nothingToHear = status === "unsupported" && !lesson.audio_url
  const atEnd = index === lastIndex
  // Heard to the end AND past the checkpoint, where there is one. An
  // unfetched checkpoint (`null`) does not block: see the catch above.
  const checkpointBlocking = Boolean(checkpoint && !checkpoint.complete)
  const mayGoOn = heardSlides.has(lesson.ordinal) && !checkpointBlocking
  const progress = ((index + 1) / detail.lessons.length) * 100

  return (
    <div className="mx-auto max-w-4xl px-5 py-8">
      {detail.previously && (
        <p className="mb-4 rounded-lg border border-line bg-sunk px-4 py-3
                      text-sm text-muted" role="status">
          You completed this on{" "}
          {new Date(detail.previously.issued_at).toLocaleDateString(undefined,
            { day: "numeric", month: "long", year: "numeric" })}
          {detail.previously.cycle_name
            ? ` for ${detail.previously.cycle_name}` : ""}.
          {detail.cycle ? ` ${detail.cycle.name} is now due` : " It is due again"}
          {detail.cycle?.due_at ? `, by ${dueDate(detail.cycle.due_at)}.` : "."}
          {" "}That certificate is still yours and still downloadable.
        </p>
      )}

      {/* Said plainly, every time. An exception that looks from the inside
          exactly like not having passed is one that gets mistaken for a bug in
          the lock — and one that nobody notices is still switched on. */}
      {detail.reviewing && (
        <p className="mb-4 rounded-lg border border-line bg-sunk px-4 py-3
                      text-sm text-muted" role="status">
          You have already passed this course. You are seeing it because your
          address is listed in <code>CONTENT_REVIEWERS</code>. Nothing here is
          being recorded against you, and the knowledge check stays closed.
        </p>
      )}

      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-lg font-semibold">{detail.title}</h1>
        <span className="text-sm text-muted tabular-nums">
          Slide {index + 1} of {detail.lessons.length}
        </span>
      </div>
      <div className="mt-2 h-1 rounded-full bg-sunk" role="presentation">
        <div className="h-1 rounded-full bg-accent transition-[width]"
             style={{ width: `${progress}%` }} />
      </div>

      <figure className="mt-5 overflow-hidden rounded-xl border border-line bg-surface shadow-[var(--shadow)]">
        <img
          src={`/media/${lesson.image}`}
          alt={lesson.title}
          className="block w-full"
        />
      </figure>

      {lesson.audio_url && (
        <audio
          ref={audio}
          // Keyed by the slide, or the browser keeps the previous recording
          // loaded and plays it against the new artwork.
          key={lesson.ordinal}
          src={mediaUrl(lesson.audio_url)}
          muted={muted}
          preload="auto"
          onPlay={() => {
            // Playback can start without the page asking for it: a headset
            // button, the keyboard's media keys, the notification-area
            // controls. The frame loop is started here rather than only where
            // the play button is handled, so the bar follows the audio however
            // it was started \u2014 otherwise it sits frozen while the voice runs.
            setStatus("speaking")
            startFollowing()
          }}
          onLoadedMetadata={(event) => {
            // The estimate on the card is the word count plus the pauses; the
            // file's own length is better, and known by now.
            const total = event.currentTarget.duration
            if (Number.isFinite(total)) {
              setHeard((was) => ({ ...was, total }))
            }
          }}
          onEnded={() => {
            stopFollowing()
            setStatus("idle")
            setSpokenAt(-1)
            setHeard((was) => ({ ...was, fraction: 1, seconds: was.total }))
            heardToTheEnd()
            // Not `step`, so the checkpoint gate has to be repeated here.
            if (!autoAdvanceNow.current || stopHereNow.current) return
            if (index < lastIndex) goTo(index + 1)
            else setReachedEnd(true)
          }}
          onError={() => {
            // The file is missing or will not decode. Say the slide with the
            // synthesiser rather than sitting in silence.
            stopFollowing()
            setStatus("idle")
            narrator.current.speak(lesson.narration, {
              onStatus: setStatus,
              onWord: setSpokenAt,
              onEnd: () => {
                heardToTheEnd()
                if (!autoAdvanceNow.current || stopHereNow.current) return
                const next = indexNow.current + 1
                if (next <= lastIndex) goTo(next)
                else setReachedEnd(true)
              },
            })
          }}
        />
      )}

      {/* How far through the narration. Under the slide, where a listener
          looks for it, and separate from the slide counter at the top: one
          says where you are in this minute and a half, the other where you
          are in the course. */}
      {lesson.narration && (
        <div className="mt-4 flex items-center gap-3">
          <div
            className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunk"
            role="progressbar"
            aria-label="Narration progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(heard.fraction * 100)}
          >
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Math.min(100, heard.fraction * 100)}%` }}
            />
          </div>
          <span className="shrink-0 text-xs text-muted tabular-nums">
            {clock(heard.seconds)} / {clock(heard.total || lesson.narration_seconds)}
          </span>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => step(-1)}
          disabled={index === 0}
          className="rounded-lg border border-line px-3 py-2 disabled:opacity-40"
          aria-label="Previous slide"
        >
          <ChevronLeft size={18} />
        </button>

        <button
          type="button"
          onClick={togglePlay}
          disabled={status === "unsupported" || !lesson.narration}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2
                     text-sm font-medium text-white disabled:opacity-40"
        >
          {playing ? <Pause size={17} /> : <Play size={17} />}
          {playing ? "Pause" : status === "paused" ? "Resume" : "Play narration"}
        </button>

        <button
          type="button"
          onClick={() => step(1)}
          disabled={atEnd || !mayGoOn}
          className="rounded-lg border border-line px-3 py-2 disabled:opacity-40"
          aria-label="Next slide"
          // Why it is greyed out, for anybody who would otherwise think the
          // page had stopped working.
          title={atEnd ? "The last slide"
                 : mayGoOn ? "Next slide"
                 : checkpointBlocking
                   ? "Answer the two questions below to continue"
                   : "Listen to the end of this slide to continue"}
        >
          <ChevronRight size={18} />
        </button>

        <button
          type="button"
          onClick={() => setMuted(!muted)}
          className="rounded-lg border border-line px-3 py-2 text-muted"
          aria-label={muted ? "Unmute" : "Mute"}
          aria-pressed={muted}
        >
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>

        <button
          type="button"
          onClick={() => setShowTranscript(!showTranscript)}
          className="rounded-lg border border-line px-3 py-2 text-muted"
          aria-label={showTranscript ? "Hide the transcript"
                                     : "Show the transcript"}
          aria-pressed={showTranscript}
        >
          <Captions size={18} />
        </button>

        <label className="ml-auto flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={autoAdvance}
            onChange={(event) => setAutoAdvance(event.target.checked)}
          />
          Advance automatically
        </label>
      </div>

      {/* THE QUESTIONS THAT STAND BETWEEN THIS SLIDE AND THE NEXT.
          Rendered under the slide rather than over it: a modal would cover the
          material the questions are about, and somebody who wants to check
          what was said should be able to look at it. */}
      {checkpoint && checkpoint.questions.length > 0 && (
        <Checkpoint slug={slug} state={checkpoint}
                    onComplete={setCheckpoint} />
      )}

      <section className="mt-6 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-xl font-semibold tracking-tight">{lesson.title}</h2>
        {nothingToHear && (
          // Said out loud rather than left as silence. A learner on a machine
          // with no voices should know the narration is missing, not conclude
          // that this slide has none.
          <p className="mt-3 rounded-lg bg-sunk px-3 py-2 text-sm text-muted">
            This browser has no speech voices available, so the narration is
            shown as text below. Everything in the course is here in writing.
          </p>
        )}
        {lesson.narration ? (
          <div className="mt-3">
            {/* Shown when asked for, and forced on when there is nothing to
                hear — a slide that is both silent and blank teaches nobody. */}
            {(showTranscript || nothingToHear) && (
              <Transcript narration={lesson.narration} spokenAt={spokenAt} />
            )}
            <p className={`text-sm text-muted tabular-nums ${
              showTranscript || nothingToHear ? "mt-3" : ""}`}>
              About {Math.round(lesson.narration_seconds / 5) * 5} seconds
              {lesson.audio_url
                ? " · recorded narration"
                : narrator.current.voiceName && status !== "unsupported"
                  ? ` · read by ${narrator.current.voiceName}` : ""}
              {!showTranscript && !nothingToHear && (
                <>
                  {" · "}
                  <button
                    type="button"
                    onClick={() => setShowTranscript(true)}
                    className="underline underline-offset-4 hover:text-text"
                  >
                    Show the words
                  </button>
                </>
              )}
            </p>
          </div>
        ) : (
          <p className="mt-3 text-muted">{lesson.body}</p>
        )}
      </section>

      {(atEnd || reachedEnd) && detail.question_count > 0 && (
        <div className="mt-6 rounded-xl border border-accent bg-accent-soft p-5">
          <h2 className="font-semibold">Ready for the knowledge check?</h2>
          <p className="mt-1 text-sm">
            {detail.question_count} questions
            {detail.question_bank > detail.question_count &&
              `, drawn at random from ${detail.question_bank}`}. You will be
            told why each answer is right, whichever way you went.
          </p>
          <Link
            to={`/module/${slug}/check`}
            onClick={() => narrator.current.stop()}
            className="mt-3 inline-block rounded-lg bg-accent px-4 py-2 text-sm
                       font-medium text-white"
          >
            Start the knowledge check
          </Link>
        </div>
      )}

      <button
        type="button"
        onClick={() => { narrator.current.stop(); navigate("/") }}
        className="mt-8 text-sm text-muted underline underline-offset-4"
      >
        Back to your training
      </button>
    </div>
  )
}
