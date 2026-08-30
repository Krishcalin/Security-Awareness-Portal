import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router"
import {
  ChevronLeft, ChevronRight, Pause, Play, Volume2, VolumeX,
} from "lucide-react"

import { api } from "../api/client"
import type { Lesson, ModuleDetail } from "../api/types"
import { Narrator, type NarratorStatus } from "../narration"

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
  const [muted, setMuted] = useState(false)
  const [autoAdvance, setAutoAdvance] = useState(true)
  const [reachedEnd, setReachedEnd] = useState(false)

  const narrator = useRef(new Narrator())
  const audio = useRef<HTMLAudioElement | null>(null)
  const marks = useRef<Mark[]>([])
  const following = useRef<number | null>(null)
  // Read inside callbacks that outlive the render they were made in.
  const autoAdvanceNow = useRef(autoAdvance)
  autoAdvanceNow.current = autoAdvance
  const lastIndex = (detail?.lessons.length ?? 1) - 1
  const indexNow = useRef(index)
  indexNow.current = index

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

  const followRecording = useCallback(() => {
    const element = audio.current
    if (!element || element.paused || element.ended) return
    const at = spokenIndex(marks.current, element.currentTime * 1000)
    setSpokenAt(at >= 0 ? marks.current[at].at : -1)
    following.current = requestAnimationFrame(followRecording)
  }, [])

  const stopFollowing = useCallback(() => {
    if (following.current !== null) {
      cancelAnimationFrame(following.current)
      following.current = null
    }
  }, [])

  useEffect(() => stopFollowing, [stopFollowing])

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
    narrator.current.speak(target.narration, {
      onStatus: setStatus,
      onWord: setSpokenAt,
      onEnd: () => {
        if (!autoAdvanceNow.current) return
        const next = indexNow.current + 1
        if (next <= lastIndex) goTo(next)
        else setReachedEnd(true)
      },
    })
  }, [detail, goTo, lastIndex, followRecording])

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
        void audio.current?.play().then(followRecording)
        setStatus("speaking")
      } else {
        narrator.current.resume()
      }
    } else {
      speakLesson(index)
    }
  }, [followRecording, index, lesson, playing, speakLesson, status,
      stopFollowing])

  // Advancing while speaking should carry the voice to the new slide rather
  // than leaving the previous one talking over it.
  const step = useCallback((delta: number) => {
    const next = Math.min(Math.max(index + delta, 0), lastIndex)
    if (next === index) return
    const wasPlaying = playing
    goTo(next)
    if (wasPlaying) window.setTimeout(() => speakLesson(next), 60)
  }, [goTo, index, lastIndex, playing, speakLesson])

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
  if (!detail || !lesson) {
    return <p className="mx-auto max-w-4xl px-5 py-10 text-muted">Loading…</p>
  }

  const atEnd = index === lastIndex
  const progress = ((index + 1) / detail.lessons.length) * 100

  return (
    <div className="mx-auto max-w-4xl px-5 py-8">
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
          onEnded={() => {
            stopFollowing()
            setStatus("idle")
            setSpokenAt(-1)
            if (!autoAdvanceNow.current) return
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
                if (!autoAdvanceNow.current) return
                const next = indexNow.current + 1
                if (next <= lastIndex) goTo(next)
                else setReachedEnd(true)
              },
            })
          }}
        />
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
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
          disabled={atEnd}
          className="rounded-lg border border-line px-3 py-2 disabled:opacity-40"
          aria-label="Next slide"
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

        <label className="ml-auto flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={autoAdvance}
            onChange={(event) => setAutoAdvance(event.target.checked)}
          />
          Advance automatically
        </label>
      </div>

      <section className="mt-6 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-xl font-semibold tracking-tight">{lesson.title}</h2>
        {status === "unsupported" && !lesson.audio_url && (
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
            <Transcript narration={lesson.narration} spokenAt={spokenAt} />
            <p className="mt-3 text-sm text-muted tabular-nums">
              About {Math.round(lesson.narration_seconds / 5) * 5} seconds
              {lesson.audio_url
                ? " · recorded narration"
                : narrator.current.voiceName && status !== "unsupported"
                  ? ` · read by ${narrator.current.voiceName}` : ""}
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
            {detail.question_count} questions. You will be told why each answer
            is right, whichever way you went.
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
