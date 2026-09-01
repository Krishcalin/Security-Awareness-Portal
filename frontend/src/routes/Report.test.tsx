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
    cycle: null as null | { id: number; name: string; opens_at: string
                            due_at: string | null },
    overdue: false,
    roster: null as null | {
      expected: number; never_signed_in: number
      signed_in_not_started: number; started_not_passed: number
      passed: number; off_roster: number
      never_signed_in_people: { email: string; display_name: string
                                department: string }[]
    },
    people: 29, never_opened: 4, opened: 25, stopped_partway: 4,
    reached_end: 21, passed: 21, passed_first_time: 13,
  },
  cycles: [] as { id: number; name: string; opens_at: string
                  due_at: string | null; passed: number; open: boolean }[],
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
  attention: { answered: 0, correct: 0, people: 0,
               correct_rate: null, min_answers: 20, stops: [] },
  off_roster: [] as { learner_id: number; email: string; display_name: string
                      department: string; started_at: string | null
                      completed_at: string | null; certificate: string | null }[],
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

describe("the training cycle", () => {
  it("is not mentioned on a portal that has none", async () => {
    report.mockResolvedValue(DATA)
    renderReport()
    await screen.findByText("Security Awareness Essentials")
    expect(screen.queryByText(/cycles on record/)).not.toBeInTheDocument()
  })

  it("names the period the figures are about", async () => {
    // "21 passed" is a number somebody will read as being about now. It is
    // about whichever period the report is scoped to, and the screen has to
    // say which.
    report.mockResolvedValue({
      ...DATA,
      summary: {
        ...DATA.summary,
        cycle: { id: 2, name: "2027 annual refresher",
                 opens_at: "2027-01-01T00:00:00Z",
                 due_at: "2027-03-31T23:59:59Z" },
        overdue: false,
      },
      cycles: [
        { id: 2, name: "2027 annual refresher", opens_at: "2027-01-01T00:00:00Z",
          due_at: "2027-03-31T23:59:59Z", passed: 21, open: true },
        { id: 1, name: "2026 annual refresher", opens_at: "2026-01-01T00:00:00Z",
          due_at: null, passed: 27, open: true },
      ],
    })
    renderReport()
    await screen.findByText("2027 annual refresher")
    // The line is assembled from several expressions, so it is several text
    // nodes; a matcher looking for one element never finds it.
    const said = () => document.body.textContent ?? ""
    expect(said()).toContain("2 cycles on record")
    expect(said()).toMatch(/· due 31 March 2027/)
  })

  it("says when the deadline has gone by", async () => {
    report.mockResolvedValue({
      ...DATA,
      summary: {
        ...DATA.summary,
        cycle: { id: 1, name: "2026 refresher", opens_at: "2026-01-01T00:00:00Z",
                 due_at: "2026-03-31T23:59:59Z" },
        overdue: true,
      },
      cycles: [],
    })
    renderReport()
    await screen.findByText("2026 refresher")
    expect(document.body.textContent ?? "").toMatch(/was due 31 March 2026/)
  })
})
