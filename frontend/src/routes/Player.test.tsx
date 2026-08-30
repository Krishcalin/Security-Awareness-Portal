import { render, screen, waitFor } from "@testing-library/react"
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
