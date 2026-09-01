/**
 * Who was supposed to take it.
 *
 * THE DEFECT. Every figure on the report was divided by the number of people
 * who had SIGNED IN — `count(*) FROM learner`, and a learner row is created at
 * sign-in and nowhere else. "Never opened it" therefore meant "signed in, and
 * never opened it". Somebody who ignored the training entirely appeared in
 * neither the numerator nor the denominator: not as untrained, but not at all.
 *
 * The rule these tests hold is that the screen must never quote a percentage
 * over a population it has not established. With a roster it says who was
 * expected; without one it says, in words, that it is counting sign-ins.
 */
import { render, screen, waitFor, within } from "@testing-library/react"
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

const BASE = {
  module: { slug: "essentials", title: "Security Awareness Essentials",
            content_hash: "abc" },
  summary: {
    cycle: null, overdue: false, roster: null,
    people: 3, never_opened: 1, opened: 2, stopped_partway: 1,
    reached_end: 1, passed: 1, passed_first_time: 1,
  },
  cycles: [], questions: [], slides: [], departments: [], off_roster: [],
  delivery: { issued: 1, emailed: 1, failed: 0, not_attempted: 0, failures: [] },
  thresholds: { min_answers: 20, not_discriminating: 0.95,
                material_failed: 0.4, pass_mark: 0.7 },
}

const ROSTER = {
  expected: 210, never_signed_in: 167, signed_in_not_started: 12,
  started_not_passed: 8, passed: 23, off_roster: 2,
  never_signed_in_people: [],
}

function person(over: Record<string, unknown>) {
  return {
    id: 1, email: "a@x.com", display_name: "A Person", department: "Ops",
    started_at: "2026-08-01T00:00:00Z", completed_at: null,
    furthest_ordinal: 4, attempts: 0, latest_score: null, out_of: null,
    certificate: null, issued_at: null, passed_on_attempt: null,
    signed_in: true, on_roster: true,
    ...over,
  }
}

function draw() {
  return render(
    <MemoryRouter initialEntries={["/report/essentials"]}>
      <Routes><Route path="/report/:slug" element={<Report />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  reportPeople.mockResolvedValue([])
})

describe("the population the figures are about", () => {
  it("says out loud that it is counting sign-ins when there is no roster", async () => {
    // Without this the screen shows six confident numbers over a population
    // nobody established, and the reader has no way to know.
    report.mockResolvedValue(BASE)
    draw()
    expect(await screen.findByText(/No roster has been imported/))
      .toBeInTheDocument()
    expect(screen.getByText(/not over\s+the people who were supposed to take/))
      .toBeInTheDocument()
    // And says how to fix it, on the screen where the problem is visible.
    expect(screen.getByText(/python -m server\.roster/)).toBeInTheDocument()
  })

  it("counts the people who were expected once a roster exists", async () => {
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    draw()
    expect(await screen.findByText("Expected")).toBeInTheDocument()
    expect(screen.getByText("210")).toBeInTheDocument()
    expect(screen.getByText("Never signed in")).toBeInTheDocument()
    expect(screen.getByText(/167 of 210/)).toBeInTheDocument()
    expect(screen.getByText(/invisible to every other figure on this page/))
      .toBeInTheDocument()
  })

  it("explains the two halves of a matching failure", async () => {
    // A changed email address appears twice. Saying so turns a puzzle into a
    // task; filtering it away would leave the roster tidy and the figure wrong.
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    draw()
    expect(await screen.findByText(/on no roster row/)).toBeInTheDocument()
    expect(screen.getByText(/once here, once as never signed in/))
      .toBeInTheDocument()
  })

  it("stops calling the sign-in count 'People' beside a roster", async () => {
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    draw()
    // "People: 3" next to "Expected: 210" reads as a contradiction rather than
    // as two different measurements.
    expect(await screen.findByText("Signed in")).toBeInTheDocument()
    expect(screen.queryByText("People")).not.toBeInTheDocument()
  })
})

describe("the list somebody works down", () => {
  it("distinguishes never signing in from never opening it", async () => {
    // Two different failures with two different remedies: one person needs an
    // account or a chase, the other needs reminding to open it.
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    reportPeople.mockResolvedValue([
      person({ id: 1, email: "here@x.com", started_at: null }),
      person({ id: null, email: "absent@x.com", display_name: "Absent Person",
               started_at: null, furthest_ordinal: 0, signed_in: false }),
    ])
    draw()
    // Scoped to the rows: "Never signed in" is deliberately both a figure
    // label in the panel above and a cell down here, and they are the same
    // claim about two different scopes.
    const rows = (await screen.findByText("absent@x.com")).closest("table")!
    expect(within(rows).getByText("Never signed in")).toBeInTheDocument()
    expect(within(rows).getByText("Never opened it")).toBeInTheDocument()
  })

  it("shows a dash, not 'No', for somebody who was never here", async () => {
    // "No" is a fact about somebody who turned up. For a person with no
    // account it would be a measurement of nothing.
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    reportPeople.mockResolvedValue([
      person({ id: null, email: "absent@x.com", started_at: null,
               furthest_ordinal: 0, signed_in: false }),
    ])
    draw()
    const cell = await screen.findByText("absent@x.com")
    await waitFor(() => {
      const row = cell.closest("tr")!
      expect(within(row).queryByText("No")).not.toBeInTheDocument()
      expect(within(row).getAllByText("—").length).toBeGreaterThan(0)
    })
  })

  it("marks somebody with results who is on no roster", async () => {
    report.mockResolvedValue({ ...BASE,
      summary: { ...BASE.summary, roster: ROSTER } })
    reportPeople.mockResolvedValue([
      person({ id: 2, email: "stranger@x.com", on_roster: false }),
    ])
    draw()
    expect(await screen.findByText("not on the roster")).toBeInTheDocument()
  })

  it("adds no such label when there is no roster to be off", async () => {
    // Without a roster every row is somebody who signed in, and the label
    // would be noise on every line.
    report.mockResolvedValue(BASE)
    reportPeople.mockResolvedValue([person({ signed_in: undefined,
                                             on_roster: undefined })])
    draw()
    await screen.findByText("a@x.com")
    expect(screen.queryByText("not on the roster")).not.toBeInTheDocument()
    expect(screen.queryByText("Never signed in")).not.toBeInTheDocument()
  })
})
