import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

const startAttempt = vi.fn()
const answer = vi.fn()
const finish = vi.fn()

vi.mock("../api/client", () => ({
  api: {
    startAttempt: (...args: unknown[]) => startAttempt(...args),
    answer: (...args: unknown[]) => answer(...args),
    finish: (...args: unknown[]) => finish(...args),
  },
}))

import { Check } from "./Check"

const ATTEMPT = {
  attempt_id: 7,
  attempt_no: 1,
  out_of: 2,
  answered: 0,
  questions: [
    {
      ordinal: 1,
      prompt: "Files are renaming themselves. What do you do first?",
      options: ["Shut it down", "Disconnect but leave it running"],
    },
    {
      ordinal: 2,
      prompt: "What does the padlock tell you?",
      options: ["The site is legitimate", "The connection is encrypted"],
    },
  ],
}

function renderCheck() {
  return render(
    <MemoryRouter initialEntries={["/module/essentials/check"]}>
      <Routes>
        <Route path="/module/:slug/check" element={<Check />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  startAttempt.mockResolvedValue(ATTEMPT)
})

describe("answering", () => {
  it("shows the question and its options", async () => {
    renderCheck()
    expect(await screen.findByText(ATTEMPT.questions[0].prompt)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Shut it down/ })).toBeInTheDocument()
  })

  it("will not submit until something is chosen", async () => {
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    expect(screen.getByRole("button", { name: "Submit answer" })).toBeDisabled()

    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    expect(screen.getByRole("button", { name: "Submit answer" })).toBeEnabled()
  })

  it("explains a wrong answer instead of only marking it wrong", async () => {
    answer.mockResolvedValue({
      ordinal: 1, correct: false, correct_index: 1, teaches: 11,
      explains: "Disconnect, but do not power off. Shutting down destroys "
        + "evidence that lives only in memory.",
    })
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))

    expect(await screen.findByText("Not quite")).toBeInTheDocument()
    expect(screen.getByText(/destroys evidence/)).toBeInTheDocument()
  })

  it("offers the slide the question came from", async () => {
    answer.mockResolvedValue({
      ordinal: 1, correct: false, correct_index: 1, teaches: 11,
      explains: "Disconnect, but do not power off.",
    })
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))

    const link = await screen.findByRole("link", { name: /Go back to slide 11/ })
    expect(link).toHaveAttribute("href", "/module/essentials?slide=11")
  })

  it("does not let the answer be changed once it is revealed", async () => {
    answer.mockResolvedValue({
      ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
    })
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))
    await screen.findByText("Correct")

    expect(screen.getByRole("button", { name: /Shut it down/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: /Disconnect but/ })).toBeDisabled()
  })

  it("reports how long the question was on screen", async () => {
    answer.mockResolvedValue({
      ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
    })
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))

    await waitFor(() => expect(answer).toHaveBeenCalled())
    const [attemptId, ordinal, chosen, tookMs] = answer.mock.calls[0]
    expect([attemptId, ordinal, chosen]).toEqual([7, 1, 0])
    expect(tookMs).toBeGreaterThanOrEqual(0)
  })
})

describe("the result", () => {
  it("says plainly that a first-attempt pass is the one worth having", async () => {
    finish.mockResolvedValue({
      attempt_no: 1, score: 2, out_of: 2, unanswered: 0, first_attempt: true,
    })
    answer.mockResolvedValue({
      ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
    })
    startAttempt.mockResolvedValue({ ...ATTEMPT, questions: [ATTEMPT.questions[0]] })

    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))
    await userEvent.click(await screen.findByRole("button", { name: "See your result" }))

    expect(await screen.findByText("2 out of 2")).toBeInTheDocument()
    expect(screen.getByText(/first time you saw them/)).toBeInTheDocument()
  })

  it("does not describe a third-attempt pass in the same words", async () => {
    finish.mockResolvedValue({
      attempt_no: 3, score: 2, out_of: 2, unanswered: 0, first_attempt: false,
    })
    answer.mockResolvedValue({
      ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
    })
    startAttempt.mockResolvedValue({
      ...ATTEMPT, attempt_no: 3, questions: [ATTEMPT.questions[0]],
    })

    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))
    await userEvent.click(await screen.findByRole("button", { name: "See your result" }))

    expect(await screen.findByText(/took 3 attempts/)).toBeInTheDocument()
    expect(screen.queryByText(/first time you saw them/)).not.toBeInTheDocument()
  })

  it("names the questions that went unanswered rather than dropping them", async () => {
    finish.mockResolvedValue({
      attempt_no: 1, score: 1, out_of: 3, unanswered: 2, first_attempt: true,
    })
    answer.mockResolvedValue({
      ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
    })
    startAttempt.mockResolvedValue({
      ...ATTEMPT, out_of: 3, questions: [ATTEMPT.questions[0]],
    })

    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))
    await userEvent.click(await screen.findByRole("button", { name: "See your result" }))

    expect(await screen.findByText(/2 questions went/)).toBeInTheDocument()
    expect(screen.getByText(/should not score better than trying it/))
      .toBeInTheDocument()
  })
})
