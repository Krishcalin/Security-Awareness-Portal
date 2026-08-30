import {
  act, cleanup, fireEvent, render, screen, waitFor,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const moduleDetail = vi.fn()
const recordProgress = vi.fn()

vi.mock("../api/client", () => ({
  api: {
    module: (...args: unknown[]) => moduleDetail(...args),
    recordProgress: (...args: unknown[]) => recordProgress(...args),
  },
}))

import { Player } from "./Player"

const NARRATION = "Welcome. Over the next few minutes we will talk about security."

function detail(overrides: Record<string, unknown> = {}) {
  return {
    slug: "essentials", title: "Security Awareness Essentials",
    summary: "", minutes: 32, content_hash: "abc",
    question_count: 10, question_bank: 100, enrolment: null,
    lessons: [{
      ordinal: 1, title: "Security Awareness Training", body: "",
      animation: "none", image: "slides/01-title.png",
      narration: NARRATION, audio_url: "", audio_timings_url: "",
      narration_seconds: 60, ...overrides,
    }],
  }
}

/** The transcript renders one <span> per word so it can highlight one, which
 *  defeats a plain text matcher — the words are there, just not in a single
 *  element. */
function transcriptShown(): boolean {
  return (document.body.textContent ?? "").includes("Over the next few minutes")
}

/**
 * Hand control of the animation frame and the clock to the test.
 *
 * The player advances the bar inside requestAnimationFrame, reading
 * performance.now() for the elapsed time. Faking timers gets one of those and
 * not the other; this gets both, so the assertions are exact rather than
 * approximate.
 */
function driveFrames() {
  // Unmount whatever the last test rendered, before anything below replaces
  // the animation frame.
  //
  // A player left mounted keeps its own loop going, and it re-registers
  // through the stub installed here: the frame this test then runs belongs to
  // the previous test's component, which has no recording and believes it is
  // still speaking. The bar under test never moves, and the failure looks
  // like a bug in the player rather than in the harness. Cleanup normally
  // happens after the test; here it has to happen before the next one starts
  // rewiring the world.
  cleanup()

  let pending: FrameRequestCallback | null = null
  let clockMs = 0
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    pending = callback
    return 1
  })
  vi.stubGlobal("cancelAnimationFrame", () => { pending = null })
  // Only `now` — replacing the whole of performance breaks the testing
  // library's own timing.
  vi.spyOn(performance, "now").mockImplementation(() => clockMs)

  return {
    /**
     * Wait until the player has actually asked for a frame.
     *
     * A recording starts the loop inside the resolution of play(), a
     * microtask after the click. Ticking before that means ticking an empty
     * loop, and the assertion that follows fails for a reason that has
     * nothing to do with what it is testing.
     */
    async started(within = 1000) {
      const until = Date.now() + within
      while (pending === null) {
        if (Date.now() > until) throw new Error("the frame loop never started")
        await new Promise((resolve) => setTimeout(resolve, 5))
      }
    },
    tick(ms: number, steps = 8) {
      // The loop accumulates the delta between frames, so a handful of large
      // steps sums to the same elapsed time as hundreds of small ones — and
      // each one costs a React flush.
      for (let i = 0; i < steps; i++) {
        clockMs += ms / steps
        const frame = pending
        pending = null
        if (frame) act(() => frame(clockMs))
      }
    },
  }
}

/**
 * A playable <audio> element, which jsdom does not provide.
 *
 * Every slide in the built course now has a recording, so this is the path
 * learners actually take; the synthesiser is the fallback. jsdom's
 * HTMLMediaElement throws on play() and reports duration as NaN, so the
 * properties the player reads are defined here and the test moves the
 * playhead itself.
 */
function fakeRecording(seconds: number) {
  let currentTime = 0
  let paused = true
  const define = (name: string, descriptor: PropertyDescriptor) =>
    Object.defineProperty(HTMLMediaElement.prototype, name,
                          { configurable: true, ...descriptor })
  define("duration", { get: () => seconds })
  define("paused", { get: () => paused })
  define("ended", { get: () => currentTime >= seconds })
  define("currentTime", { get: () => currentTime,
                          set: (value: number) => { currentTime = value } })
  HTMLMediaElement.prototype.play = vi.fn(() => { paused = false
                                                  return Promise.resolve() })
  HTMLMediaElement.prototype.pause = vi.fn(() => { paused = true })
  return {
    seek: (to: number) => { currentTime = to },
    // What the media keys and the notification-area controls do: the element
    // stops and starts without the page being asked.
    pauseOutside: () => { paused = true },
    resumeOutside: (element: HTMLMediaElement) => {
      paused = false
      fireEvent.play(element)
    },
  }
}

function renderPlayer() {
  return render(
    <MemoryRouter initialEntries={["/module/essentials"]}>
      <Routes>
        <Route path="/module/:slug" element={<Player />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  recordProgress.mockResolvedValue({ furthest_ordinal: 1 })
  // A machine with working speech, so nothing is forced on.
  vi.stubGlobal("speechSynthesis", {
    getVoices: () => [{ name: "Daniel", lang: "en-GB", localService: true }],
    speak() {}, cancel() {}, pause() {}, resume() {},
    speaking: false, paused: false,
    addEventListener() {}, removeEventListener() {},
  })
  vi.stubGlobal("SpeechSynthesisUtterance", class { constructor(public text: string) {} })
})

afterEach(() => vi.unstubAllGlobals())

describe("the transcript", () => {
  it("is not on screen by default", async () => {
    // The course is meant to be listened to. A wall of text beside the voice
    // invites people to read ahead instead of hearing it.
    moduleDetail.mockResolvedValue(detail())
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    expect(transcriptShown()).toBe(false)
  })

  it("can be turned on by anybody who wants it", async () => {
    moduleDetail.mockResolvedValue(detail())
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    await userEvent.click(screen.getByRole("button", { name: "Show the transcript" }))
    expect(transcriptShown()).toBe(true)
  })

  it("remembers the choice, so it is not a fight on every slide", async () => {
    // Somebody who needs the words needs them on all thirty-one.
    moduleDetail.mockResolvedValue(detail())
    const first = renderPlayer()
    await screen.findByText("Security Awareness Training")
    await userEvent.click(screen.getByRole("button", { name: "Show the transcript" }))
    first.unmount()

    renderPlayer()
    await screen.findByText("Security Awareness Training")
    expect(transcriptShown()).toBe(true)
  })

  it("is forced on when there is nothing to hear", async () => {
    // No recording and no voices on this machine. A slide that is both silent
    // AND blank teaches nobody, and this is the case that would otherwise
    // make the course impossible for somebody who cannot hear it.
    vi.stubGlobal("speechSynthesis", undefined)
    vi.stubGlobal("SpeechSynthesisUtterance", undefined)
    moduleDetail.mockResolvedValue(detail())
    renderPlayer()
    await waitFor(() => expect(transcriptShown()).toBe(true))
    expect(screen.getByText(/no speech voices available/)).toBeInTheDocument()
  })

  it("stays hidden when there is a recording, even with no voices", async () => {
    // A recording plays regardless of what voices are installed, so there is
    // something to hear and the words are optional again.
    vi.stubGlobal("speechSynthesis", undefined)
    vi.stubGlobal("SpeechSynthesisUtterance", undefined)
    moduleDetail.mockResolvedValue(detail({ audio_url: "narration/x/01.mp3" }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    expect(transcriptShown()).toBe(false)
  })
})

describe("the narration progress bar", () => {
  it("is there for a narrated slide, and starts at nothing", async () => {
    moduleDetail.mockResolvedValue(detail())
    renderPlayer()
    const bar = await screen.findByRole("progressbar", { name: "Narration progress" })
    expect(bar).toHaveAttribute("aria-valuenow", "0")
  })

  it("shows the length of the slide beside it", async () => {
    moduleDetail.mockResolvedValue(detail({ narration_seconds: 85 }))
    renderPlayer()
    // 0:00 of 1:25 — minutes and seconds, not a raw count.
    expect(await screen.findByText("0:00 / 1:25")).toBeInTheDocument()
  })

  it("fills as the synthesiser speaks", async () => {
    // The browser synthesiser reports no position at all, so the bar is driven
    // from time spoken against the slide's estimated length. That is the path
    // with nothing to measure, and therefore the one worth testing.
    //
    // The frame loop is driven by hand rather than with fake timers: the
    // component reads performance.now() inside requestAnimationFrame, and
    // controlling both directly is the difference between a deterministic
    // test and one that waits and hopes.
    const { tick } = driveFrames()
    moduleDetail.mockResolvedValue(detail({ narration_seconds: 100 }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    await userEvent.click(screen.getByRole("button", { name: /Play narration/ }))

    const bar = screen.getByRole("progressbar", { name: "Narration progress" })
    expect(bar).toHaveAttribute("aria-valuenow", "0")

    tick(25_000)                       // a quarter of the way through
    expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThan(20)
    expect(Number(bar.getAttribute("aria-valuenow"))).toBeLessThan(30)

    tick(80_000)                       // past the end
    expect(bar).toHaveAttribute("aria-valuenow", "100")
  })

  it("never runs past the end of the slide", async () => {
    // The length it is measured against is an estimate, so time spoken can
    // overrun it. A bar that keeps filling past full looks broken.
    const { tick } = driveFrames()
    moduleDetail.mockResolvedValue(detail({ narration_seconds: 10 }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    await userEvent.click(screen.getByRole("button", { name: /Play narration/ }))

    tick(60_000)
    const bar = screen.getByRole("progressbar", { name: "Narration progress" })
    expect(bar).toHaveAttribute("aria-valuenow", "100")
    expect(screen.getByText("0:10 / 0:10")).toBeInTheDocument()
  })

  it("goes back to nothing on the next slide", async () => {
    const { tick } = driveFrames()
    const two = detail({ narration_seconds: 100 })
    two.lessons.push({ ...two.lessons[0], ordinal: 2, title: "Why Security Matters" })
    moduleDetail.mockResolvedValue(two)
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    await userEvent.click(screen.getByRole("button", { name: /Play narration/ }))
    tick(30_000)
    await userEvent.click(screen.getByRole("button", { name: "Next slide" }))

    expect(screen.getByRole("progressbar", { name: "Narration progress" }))
      .toHaveAttribute("aria-valuenow", "0")

    // Stepping while the narration is playing hands the voice to the new
    // slide after a beat, so the course keeps talking rather than stopping
    // whenever somebody skips ahead. Waiting for it asserts that, and leaves
    // nothing of this test still running inside the next one.
    await screen.findByRole("button", { name: "Pause" })
  })

  it("follows the recording rather than the estimate, when there is one",
     async () => {
    // Two different numbers deliberately: the word count says this slide runs
    // a minute, the file runs two. The bar and the clock must both come from
    // the file, because that is the thing the learner is actually hearing.
    const { started, tick } = driveFrames()
    const recording = fakeRecording(120)
    moduleDetail.mockResolvedValue(detail({
      audio_url: "narration/essentials/01.mp3", narration_seconds: 60,
    }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")

    fireEvent.loadedMetadata(document.querySelector("audio")!)
    expect(screen.getByText("0:00 / 2:00")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /Play narration/ }))
    await started()
    recording.seek(30)
    tick(100)

    const bar = screen.getByRole("progressbar", { name: "Narration progress" })
    expect(bar).toHaveAttribute("aria-valuenow", "25")
    expect(screen.getByText("0:30 / 2:00")).toBeInTheDocument()
  })

  it("picks the recording back up when the media keys restart it", async () => {
    // Nothing tells the page that a headset button paused the audio, so the
    // frame loop simply finds the element stopped and ends. It has to be able
    // to start again when the audio does, or the bar stays frozen for the rest
    // of the slide while the voice carries on.
    const { started, tick } = driveFrames()
    const recording = fakeRecording(120)
    moduleDetail.mockResolvedValue(detail({
      audio_url: "narration/essentials/01.mp3", narration_seconds: 120,
    }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    const element = document.querySelector("audio")!
    fireEvent.loadedMetadata(element)

    await userEvent.click(screen.getByRole("button", { name: /Play narration/ }))
    await started()
    recording.seek(30)
    tick(100)
    const bar = screen.getByRole("progressbar", { name: "Narration progress" })
    expect(bar).toHaveAttribute("aria-valuenow", "25")

    recording.pauseOutside()
    tick(100)                          // the loop finds it stopped, and ends

    act(() => recording.resumeOutside(element))
    recording.seek(60)
    await started()
    tick(100)
    expect(bar).toHaveAttribute("aria-valuenow", "50")
  })

  it("is not drawn on a slide with nothing to narrate", async () => {
    // The quiz gate is deliberately silent; a bar that never moves is worse
    // than no bar.
    moduleDetail.mockResolvedValue(detail({ narration: "", narration_seconds: 0 }))
    renderPlayer()
    await screen.findByText("Security Awareness Training")
    expect(screen.queryByRole("progressbar", { name: "Narration progress" }))
      .not.toBeInTheDocument()
  })
})
