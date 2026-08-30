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
  // The real one, not a stub: the href it builds is the thing under test.
  certificateUrl: (serial: string) =>
    `/api/certificates/${encodeURIComponent(serial)}`,
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
      passed: true, pass_mark: 0.7, needed: 2, certificate: null,
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
      passed: true, pass_mark: 0.7, needed: 2, certificate: null,
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
      passed: false, pass_mark: 0.7, needed: 3, certificate: null,
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


describe("the certificate", () => {
  const CORRECT = {
    ordinal: 1, correct: true, correct_index: 0, teaches: 11, explains: "Yes.",
  }

  async function finishWith(result: Record<string, unknown>) {
    finish.mockResolvedValue(result)
    answer.mockResolvedValue(CORRECT)
    startAttempt.mockResolvedValue({ ...ATTEMPT, questions: [ATTEMPT.questions[0]] })
    renderCheck()
    await screen.findByText(ATTEMPT.questions[0].prompt)
    await userEvent.click(screen.getByRole("button", { name: /Shut it down/ }))
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }))
    await userEvent.click(await screen.findByRole("button", { name: "See your result" }))
  }

  const PASSED = {
    attempt_no: 1, score: 9, out_of: 12, unanswered: 0, first_attempt: true,
    passed: true, pass_mark: 0.7, needed: 9,
    certificate: {
      serial: "SAT-2026-7QK4M2XD",
      name_printed: "Krishnendu De",
      issued_at: "2026-08-30T10:00:00Z",
      will_email_to: "krishnendu.de@example.com",
    },
  }

  it("offers the certificate for download, by its own link", async () => {
    await finishWith(PASSED)
    const link = await screen.findByRole("link", { name: /Download the PDF/ })
    expect(link).toHaveAttribute("href", "/api/certificates/SAT-2026-7QK4M2XD")
  })

  it("shows the name that was printed on it", async () => {
    await finishWith(PASSED)
    expect(await screen.findByText("Krishnendu De")).toBeInTheDocument()
    expect(screen.getByText(/SAT-2026-7QK4M2XD/)).toBeInTheDocument()
  })

  it("mentions the email only when one is actually being sent", async () => {
    await finishWith(PASSED)
    expect(await screen.findByText(/krishnendu.de@example.com/)).toBeInTheDocument()
    expect(screen.getByText(/office of the CISO/)).toBeInTheDocument()
  })

  it("says nothing about email when none will be sent", async () => {
    // A portal that prints "check your inbox" for a message it never
    // posted is worse than one that says nothing about email at all.
    await finishWith({
      ...PASSED,
      certificate: { ...PASSED.certificate, will_email_to: "" },
    })
    expect(await screen.findByRole("link", { name: /Download the PDF/ }))
      .toBeInTheDocument()
    expect(screen.queryByText(/on its way/)).not.toBeInTheDocument()
  })

  it("offers no certificate when the mark was not reached", async () => {
    await finishWith({
      attempt_no: 1, score: 8, out_of: 12, unanswered: 0, first_attempt: true,
      passed: false, pass_mark: 0.7, needed: 9, certificate: null,
    })
    expect(await screen.findByText("8 out of 12")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: /Download the PDF/ }))
      .not.toBeInTheDocument()
    expect(screen.getByText(/needed 9 of 12 to pass/)).toBeInTheDocument()
  })
})
