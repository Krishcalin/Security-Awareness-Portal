import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

const report = vi.fn()
const reportPeople = vi.fn()

vi.mock("../api/client", () => ({
  api: {
    report: (...args: unknown[]) => report(...args),
    reportPeople: (...args: unknown[]) => reportPeople(...args),
  },
  reportExportUrl: (slug: string) => `/api/report/${slug}/export.csv`,
}))

import { Report } from "./Report"

const DATA = {
  module: { slug: "essentials", title: "Security Awareness Essentials",
            content_hash: "abc" },
  summary: {
    people: 29, never_opened: 4, opened: 25, stopped_partway: 4,
    reached_end: 21, passed: 21, passed_first_time: 13,
  },
  questions: [
    { question_id: 1, ordinal: 1, prompt: "Everybody gets this one",
      teaches: 3, teaches_title: "Phishing Emails", answered: 40, correct: 40,
      correct_rate: 1.0, guessed: 2,
      verdict: "everybody gets this right — it measures nothing" },
    { question_id: 2, ordinal: 2, prompt: "Almost nobody gets this one",
      teaches: 11, teaches_title: "Ransomware: What To Do", answered: 40,
      correct: 6, correct_rate: 0.15, guessed: 1,
      verdict: "most people get this wrong — look at the slide" },
    { question_id: 3, ordinal: 3, prompt: "A question that works",
      teaches: 5, teaches_title: "Password Best Practices", answered: 40,
      correct: 24, correct_rate: 0.6, guessed: 0, verdict: "discriminating" },
  ],
  slides: [
    { ordinal: 6, title: "Multi-Factor Authentication", stopped_here: 2, reached: 25 },
    { ordinal: 3, title: "Phishing Emails", stopped_here: 1, reached: 27 },
  ],
  departments: [],
  delivery: { issued: 21, emailed: 0, failed: 21, not_attempted: 0, failures: [] },
  thresholds: { min_answers: 20, not_discriminating: 0.95,
                material_failed: 0.4, pass_mark: 0.7 },
}

function renderReport() {
  return render(
    <MemoryRouter initialEntries={["/report/essentials"]}>
      <Routes>
        <Route path="/report/:slug" element={<Report />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  report.mockResolvedValue(DATA)
  reportPeople.mockResolvedValue([])
})

describe("the headline", () => {
  it("shows completion and result as separate numbers", async () => {
    renderReport()
    expect(await screen.findByText("Reached the end")).toBeInTheDocument()
    expect(screen.getByText("Passed the check")).toBeInTheDocument()
    // They happen to be equal here, and they are still two figures: everyone
    // who reached the end passed. Collapsing them would lose that.
    expect(screen.getAllByText("21")).toHaveLength(2)
  })

  it("gives passing first time its own place", async () => {
    // A pass on the third attempt is not the same evidence as a pass on the
    // first, and this is the figure that gets left out of every report that
    // reduces to one number.
    renderReport()
    expect(await screen.findByText("Passed first time")).toBeInTheDocument()
    expect(screen.getByText("13")).toBeInTheDocument()
  })

  it("never renders a single percentage that reads as a verdict", async () => {
    // "94% trained" is the number this whole screen exists not to produce.
    renderReport()
    const heading = await screen.findByText("Where people are")
    const section = heading.closest("section")!
    const figures = section.querySelectorAll(".text-2xl")
    expect(figures.length).toBe(6)
    for (const figure of figures) {
      expect(figure.textContent).not.toMatch(/%/)
    }
  })

  it("says people who never opened it are not the same as people who stopped", async () => {
    renderReport()
    expect(await screen.findByText("Never opened it")).toBeInTheDocument()
    expect(screen.getByText("Stopped part way")).toBeInTheDocument()
  })
})

describe("the questions", () => {
  it("lists the ones that are not telling anybody apart", async () => {
    renderReport()
    expect(await screen.findByText("Everybody gets this one")).toBeInTheDocument()
    expect(screen.getByText(/measures nothing/)).toBeInTheDocument()
  })

  it("names the slide behind a question most people get wrong", async () => {
    renderReport()
    expect(await screen.findByText(/Slide 11 — Ransomware: What To Do/))
      .toBeInTheDocument()
  })

  it("leaves a working question out of the list of problems", async () => {
    renderReport()
    await screen.findByText("Everybody gets this one")
    expect(screen.queryByText("A question that works")).not.toBeInTheDocument()
  })
})

describe("certificates", () => {
  it("surfaces the ones that never actually went anywhere", async () => {
    renderReport()
    expect(await screen.findByText(/21 that failed to send/)).toBeInTheDocument()
  })
})
