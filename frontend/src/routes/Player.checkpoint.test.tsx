/**
 * The two questions that stand between slide five and slide six.
 *
 * WHY THE GATE IS NOT ENOUGH ON ITS OWN. The forward arrow already waits for
 * the narration to finish, which proves the audio reached the end — and a
 * course left playing to an empty chair reaches the end exactly as one that
 * was watched. The checkpoint is the smallest honest test that somebody is
 * still there.
 *
 * IT TEACHES, IT DOES NOT MARK. A wrong answer shows the right one and the
 * explanation and then lets you past. These tests hold that line: getting both
 * wrong must open the way forward, or six checkpoints become six exams and the
 * first thing anybody does is stop answering honestly.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const moduleDetail = vi.fn()
const recordProgress = vi.fn()
const checkpoint = vi.fn()
const answerCheckpoint = vi.fn()

vi.mock("../api/client", () => ({
  api: {
    module: (...a: unknown[]) => moduleDetail(...a),
    recordProgress: (...a: unknown[]) => recordProgress(...a),
    checkpoint: (...a: unknown[]) => checkpoint(...a),
    answerCheckpoint: (...a: unknown[]) => answerCheckpoint(...a),
  },
  ApiError: class ApiError extends Error {
    constructor(readonly status: number, message: string) { super(message) }
  },
}))

import { Player } from "./Player"

const NARRATION = "Welcome. We will talk about security."

/** A course of `count` recorded slides. Six by default, so slide 5 has a
 *  checkpoint after it and slide 6 exists to be blocked from. */
function course(count = 6) {
  return {
    slug: "essentials", title: "Security Awareness Essentials",
    summary: "", minutes: 5, completed: null, cycle: null, previously: null,
    reviewing: false,
    enrolment: { furthest_ordinal: 0, last_ordinal: 0 },
    lessons: Array.from({ length: count }, (_, at) => ({
      ordinal: at + 1, title: `Slide ${at + 1}`, body: "", animation: "none",
      image: `slides/0${at + 1}.png`, narration: NARRATION,
      audio_url: `narration/essentials/0${at + 1}.mp3`,
      audio_timings_url: "", narration_seconds: 90,
    })),
  }
}

function state(overrides: Record<string, unknown> = {}) {
  return {
    after_ordinal: 5,
    questions: [
      { position: 0, ordinal: 12, prompt: "What is phishing?",
        options: ["A fish", "A fraudulent message", "A firewall"] },
      { position: 1, ordinal: 19, prompt: "Where do you report one?",
        options: ["Nowhere", "The service desk"] },
    ],
    complete: false,
    ...overrides,
  }
}

// jsdom has no playable <audio>; the real suite defines one the same way.
let audioCleanup: (() => void) | null = null
function makeAudioPlayable() {
  const proto = window.HTMLMediaElement.prototype
  const saved = { play: proto.play, pause: proto.pause }
  proto.play = vi.fn().mockResolvedValue(undefined) as never
  proto.pause = vi.fn() as never
  audioCleanup = () => { proto.play = saved.play; proto.pause = saved.pause }
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

const nextSlide = () => screen.getByRole("button", { name: "Next slide" })

/** Listen to the current slide to the end, without auto-advance carrying the
 *  course on by itself. */
async function listenToTheEnd() {
  fireEvent.ended(document.querySelector("audio")!)
}

/** Render, wait for the first slide, and stop the course advancing by itself
 *  — auto-advance would carry it past the very button under test. */
async function start() {
  renderPlayer()
  await screen.findByText("Slide 1")
  await userEvent.click(screen.getByLabelText("Advance automatically"))
}

async function walkTo(ordinal: number) {
  for (let at = 1; at < ordinal; at++) {
    await screen.findByText(`Slide ${at}`)
    await listenToTheEnd()
    await userEvent.click(nextSlide())
  }
  await screen.findByText(`Slide ${ordinal}`)
}

beforeEach(async () => {
  vi.clearAllMocks()
  makeAudioPlayable()
  moduleDetail.mockResolvedValue(course())
  recordProgress.mockResolvedValue({ furthest_ordinal: 1 })
  checkpoint.mockResolvedValue(state())
  answerCheckpoint.mockImplementation(
    async (_slug: string, _after: number, position: number, chosen: number) => ({
      position, chosen_index: chosen, correct: chosen === 1, correct_index: 1,
      explains: "A fraudulent message that tries to look legitimate.",
      teaches: 3,
    }))
})

afterEach(() => { audioCleanup?.(); audioCleanup = null })

describe("a checkpoint every five slides", () => {
  it("asks nothing on a slide that is not a fifth", async () => {
    await start()
    await listenToTheEnd()
    await waitFor(() => expect(nextSlide()).toBeEnabled())
    expect(screen.queryByText("Quick check")).not.toBeInTheDocument()
    expect(checkpoint).not.toHaveBeenCalled()
  })

  it("does not ask until the slide has actually been heard", async () => {
    // Two questions about material somebody is still listening to, on the
    // screen underneath them, would be asking about what has not been said.
    await start()
    expect(checkpoint).not.toHaveBeenCalled()
  })

  it("blocks the way on until both questions are answered", async () => {
    await start()
    await walkTo(5)
    await listenToTheEnd()

    expect(await screen.findByText("Quick check")).toBeInTheDocument()
    await waitFor(() => expect(nextSlide()).toBeDisabled())
    // And says WHICH gate is shut. "Listen to the end" on a slide they have
    // listened to the end of reads as a broken page.
    expect(nextSlide()).toHaveAttribute(
      "title", "Answer the two questions below to continue")
  })

  it("opens the way on once both are answered, right or wrong", async () => {
    await start()
    await walkTo(5)
    await listenToTheEnd()
    await screen.findByText("Quick check")

    // Deliberately the WRONG option for the first: a checkpoint that could
    // fail somebody is an exam, and this course has six of them.
    await userEvent.click(screen.getByRole("button", { name: /A fish/ }))
    await userEvent.click(screen.getByRole("button", { name: /service desk/ }))

    await waitFor(() => expect(nextSlide()).toBeEnabled())
  })

  it("shows the right answer with its explanation when they get it wrong", async () => {
    await start()
    await walkTo(5)
    await listenToTheEnd()
    await screen.findByText("Quick check")

    await userEvent.click(screen.getByRole("button", { name: /A fish/ }))
    expect(await screen.findByText(/fraudulent message that tries to look/))
      .toBeInTheDocument()
  })

  it("refuses the arrow key as well as the button", async () => {
    // Otherwise the gate is a greyed-out button with a keyboard shortcut
    // around it.
    await start()
    await walkTo(5)
    await listenToTheEnd()
    await screen.findByText("Quick check")

    fireEvent.keyDown(window, { key: "ArrowRight" })
    expect(screen.getByText("Slide 5")).toBeInTheDocument()
  })

  it("lets somebody who already answered it walk straight past", async () => {
    // A learner who answered on their phone this morning does not answer
    // again this afternoon: whether it is done is the server's answer.
    checkpoint.mockResolvedValue(state({ complete: true,
      questions: state().questions.map((q) => ({
        ...q, answered: { position: q.position, chosen_index: 1, correct: true,
                          correct_index: 1, explains: "Because.", teaches: 3 } })) }))
    await start()
    await walkTo(5)
    await listenToTheEnd()

    await waitFor(() => expect(nextSlide()).toBeEnabled())
  })

  it("does not become a locked door when the checkpoint cannot be fetched", async () => {
    // The course is what somebody came for. A network failure between two
    // slides is not a reason to end their training.
    checkpoint.mockRejectedValue(new Error("offline"))
    await start()
    await walkTo(5)
    await listenToTheEnd()

    await waitFor(() => expect(nextSlide()).toBeEnabled())
    expect(screen.queryByText("Quick check")).not.toBeInTheDocument()
  })

  it("never asks after the last slide", async () => {
    // A checkpoint there is the knowledge check.
    moduleDetail.mockResolvedValue(course(5))
    await start()
    await walkTo(5)
    await listenToTheEnd()

    await waitFor(() => expect(checkpoint).not.toHaveBeenCalled())
    expect(screen.queryByText("Quick check")).not.toBeInTheDocument()
  })
})

describe("with auto-advance left ON, which is the default", () => {
  /**
   * THE DEFECT THIS COVERS, REPORTED FROM A REAL RUN: "I am at slide 8 and I
   * am still not getting the Q&A."
   *
   * The forward gate lives in `step`. Auto-advance does not go through `step`
   * — when a recording ends it calls `goTo` itself, from three separate
   * callbacks — so slide five ended, the checkpoint was fetched, and the
   * player moved to slide six before anybody could see it.
   *
   * Every test above turned auto-advance OFF as its first act, because
   * otherwise the course walks past the button under test. That helper
   * disabled the exact thing that was broken, so nine passing tests said
   * nothing about the default path. These do not touch the setting.
   */
  async function play() {
    renderPlayer()
    await screen.findByText("Slide 1")
  }

  /** Let each recording end; auto-advance carries the course on by itself. */
  async function playThrough(upTo: number) {
    for (let at = 1; at < upTo; at++) {
      await screen.findByText(`Slide ${at}`)
      fireEvent.ended(document.querySelector("audio")!)
    }
    await screen.findByText(`Slide ${upTo}`)
  }

  it("stops at the checkpoint instead of walking past it", async () => {
    await play()
    await playThrough(5)
    fireEvent.ended(document.querySelector("audio")!)

    expect(await screen.findByText("Quick check")).toBeInTheDocument()
    // Still on five. Before the fix this was slide six, and by slide eight the
    // learner had been carried past two checkpoints without seeing either.
    expect(screen.getByText("Slide 5")).toBeInTheDocument()
    expect(screen.queryByText("Slide 6")).not.toBeInTheDocument()
  })

  it("does not reach slide eight without asking", async () => {
    // The report as it arrived, turned into an assertion.
    await play()
    await playThrough(5)
    fireEvent.ended(document.querySelector("audio")!)
    await screen.findByText("Quick check")

    for (let n = 0; n < 4; n++) {
      fireEvent.ended(document.querySelector("audio")!)
    }
    expect(screen.getByText("Slide 5")).toBeInTheDocument()
  })

  it("still carries the course on between ordinary slides", async () => {
    // The fix must not turn auto-advance off in general; it only has to hold
    // at a checkpoint.
    await play()
    await playThrough(4)
    expect(screen.getByText("Slide 4")).toBeInTheDocument()
    expect(checkpoint).not.toHaveBeenCalled()
  })

  it("carries on again once the questions are answered", async () => {
    await play()
    await playThrough(5)
    fireEvent.ended(document.querySelector("audio")!)
    await screen.findByText("Quick check")

    await userEvent.click(screen.getByRole("button", { name: /A fish/ }))
    await userEvent.click(screen.getByRole("button", { name: /service desk/ }))

    // Deliberately NOT automatic: the explanation is the teaching moment, and
    // whisking somebody away from it a moment after they answer destroys the
    // thing the checkpoint exists to do. The way on opens; they take it.
    await waitFor(() => expect(nextSlide()).toBeEnabled())
    await userEvent.click(nextSlide())
    expect(await screen.findByText("Slide 6")).toBeInTheDocument()
  })
})
