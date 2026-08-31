import { useEffect, useState } from "react"
import { useParams } from "react-router"
import { AlertTriangle, Download, MailWarning } from "lucide-react"

import { api, reportExportUrl } from "../api/client"
import { dueDate } from "../format"
import type { Report as ReportData, ReportPerson } from "../api/types"

/**
 * What the training actually shows.
 *
 * The screen exists to avoid producing one number. "94% trained" fits on a
 * slide, gets pasted into a board pack, and is read as evidence that people
 * can spot a phish — when all it says is that they reached the last page. So
 * completion and result are laid out side by side and never combined, and the
 * figure that carries the most evidence, passing first time, is given its own
 * place rather than being buried.
 *
 * The second half of the screen reports on the QUESTIONS rather than on the
 * people, which is the part most reporting leaves out: a question everybody
 * answers correctly measures nothing, and one almost nobody gets is usually a
 * fact about the slide.
 */
export function Report() {
  const { slug = "" } = useParams()
  const [data, setData] = useState<ReportData | null>(null)
  const [people, setPeople] = useState<ReportPerson[] | null>(null)
  const [problem, setProblem] = useState("")

  useEffect(() => {
    api.report(slug).then(setData).catch((error) => setProblem(error.message))
    api.reportPeople(slug).then(setPeople).catch(() => {
      // The headline is worth showing even if the roll is not.
    })
  }, [slug])

  if (problem) {
    return <p className="mx-auto max-w-6xl px-5 py-10 text-wrong">{problem}</p>
  }
  if (!data) {
    return <p className="mx-auto max-w-6xl px-5 py-10 text-muted">Loading…</p>
  }

  const s = data.summary
  const notWorking = data.questions.filter(
    (q) => q.verdict.includes("measures nothing") ||
           q.verdict.includes("look at the slide"))
  const stalls = [...data.slides]
    .filter((slide) => slide.stopped_here > 0)
    .sort((a, b) => b.stopped_here - a.stopped_here)
    .slice(0, 6)

  return (
    <div className="mx-auto max-w-6xl px-5 py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {data.module.title}
          </h1>
          <p className="mt-1 text-muted">
            Individual results, not anonymous statistics.
          </p>
          {/* Which period the figures below are about. Without it "3 of 40
              passed" is a number somebody will read as being about now when
              it is about a year that closed in March. */}
          {data.summary.cycle && (
            <p className="mt-2 text-sm">
              <span className="font-medium">{data.summary.cycle.name}</span>
              {data.summary.cycle.due_at && (
                <span className={data.summary.overdue ? "text-wrong"
                                                      : "text-muted"}>
                  {" · "}
                  {data.summary.overdue ? "was due " : "due "}
                  {dueDate(data.summary.cycle.due_at)}
                </span>
              )}
              {data.cycles.length > 1 && (
                <span className="text-muted">
                  {" · "}{data.cycles.length} cycles on record
                </span>
              )}
            </p>
          )}
        </div>
        <a
          href={reportExportUrl(slug)}
          className="inline-flex items-center gap-2 rounded-lg border border-line
                     px-4 py-2 text-sm"
        >
          <Download size={16} aria-hidden />
          Export the record
        </a>
      </div>

      {/* ── the headline, deliberately as six numbers ─────────────────── */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
          Where people are
        </h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Figure label="People" value={s.people} />
          <Figure label="Never opened it" value={s.never_opened} />
          <Figure label="Stopped part way" value={s.stopped_partway} />
          <Figure label="Reached the end" value={s.reached_end} />
          <Figure label="Passed the check" value={s.passed} />
          <Figure label="Passed first time" value={s.passed_first_time} strong />
        </div>
        <p className="mt-3 max-w-prose text-sm text-muted">
          These are not combined into a single figure on purpose. Reaching the
          end means somebody saw every slide; passing means they could answer
          questions about them. <strong className="text-text">Passing first
          time</strong> is the one that carries the most evidence — a pass on
          the third attempt is a different thing.
        </p>
      </section>

      {/* ── the questions ────────────────────────────────────────────── */}
      <section className="mt-10">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
          Questions worth changing
        </h2>
        {notWorking.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            Nothing to report yet. A question needs{" "}
            {data.thresholds.min_answers} answers before its correct rate means
            anything, and below that it is not reported at all.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead className="bg-sunk text-left text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="p-3">Question</th>
                  <th className="p-3">Teaches</th>
                  <th className="p-3 text-right tabular-nums">Answered</th>
                  <th className="p-3 text-right tabular-nums">Correct</th>
                  <th className="p-3">What that means</th>
                </tr>
              </thead>
              <tbody>
                {notWorking.map((q) => (
                  <tr key={q.question_id} className="border-t border-line align-top">
                    <td className="p-3 max-w-md">{q.prompt}</td>
                    <td className="p-3 text-muted">
                      {q.teaches ? `Slide ${q.teaches} — ${q.teaches_title}` : "—"}
                    </td>
                    <td className="p-3 text-right tabular-nums">{q.answered}</td>
                    <td className="p-3 text-right tabular-nums">
                      {q.correct_rate === null
                        ? "—"
                        : `${Math.round(q.correct_rate * 100)}%`}
                    </td>
                    <td className="p-3">
                      <span className="inline-flex items-start gap-1.5">
                        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-wrong"
                                       aria-hidden />
                        {q.verdict}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 max-w-prose text-sm text-muted">
          A question everybody answers correctly cannot tell somebody who
          understands from somebody who does not, so a high score on it is not
          evidence of awareness. One almost nobody gets right is usually a fact
          about the slide, which is why the slide is named beside it.
        </p>
      </section>

      {/* ── where people stop ────────────────────────────────────────── */}
      {stalls.length > 0 && (
        <section className="mt-10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
            Where people stop
          </h2>
          <ul className="mt-3 grid gap-2">
            {stalls.map((slide) => (
              <li key={slide.ordinal}
                  className="flex items-center justify-between gap-4 rounded-lg
                             border border-line bg-surface px-4 py-2.5 text-sm">
                <span>Slide {slide.ordinal} — {slide.title}</span>
                <span className="tabular-nums text-muted">
                  {slide.stopped_here} stopped here
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── certificates that never left ─────────────────────────────── */}
      {(data.delivery.failed > 0 || data.delivery.not_attempted > 0) && (
        <section className="mt-10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
            Certificates
          </h2>
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-line
                        bg-surface px-4 py-3 text-sm">
            <MailWarning size={17} className="mt-0.5 shrink-0 text-wrong" aria-hidden />
            <span>
              {data.delivery.issued} issued, {data.delivery.emailed} emailed
              {data.delivery.failed > 0 &&
                `, ${data.delivery.failed} that failed to send`}
              {data.delivery.not_attempted > 0 &&
                `, ${data.delivery.not_attempted} never attempted`}.
              An email that bounced and one that was never sent look identical
              from the outside, and both look like a certificate that arrived.
            </span>
          </p>
        </section>
      )}

      {/* ── the roll ─────────────────────────────────────────────────── */}
      {people && people.length > 0 && (
        <section className="mt-10">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
            Everyone
          </h2>
          <div className="mt-3 overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead className="bg-sunk text-left text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="p-3">Person</th>
                  <th className="p-3">Department</th>
                  <th className="p-3">Got as far as</th>
                  <th className="p-3">Reached the end</th>
                  <th className="p-3 text-right tabular-nums">Score</th>
                  <th className="p-3">Completed successfully</th>
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <tr key={person.id} className="border-t border-line">
                    <td className="p-3">
                      <div>{person.display_name || person.email}</div>
                      <div className="text-xs text-muted">{person.email}</div>
                    </td>
                    <td className="p-3 text-muted">{person.department || "—"}</td>
                    <td className="p-3 text-muted">
                      {person.started_at
                        ? `Slide ${person.furthest_ordinal}`
                        : "Never opened it"}
                    </td>
                    <td className="p-3 text-muted">
                      {person.completed_at ? "Yes" : "No"}
                    </td>
                    <td className="p-3 text-right tabular-nums">
                      {person.latest_score === null
                        ? "—"
                        : `${person.latest_score} of ${person.out_of}`}
                    </td>
                    <td className="p-3">
                      {person.certificate ? (
                        <>
                          {/* The date is the answer to the question an
                              auditor actually asks, which is not "did they"
                              but "when, and can I see the document". */}
                          <div className="text-right tabular-nums">
                            {person.issued_at
                              ? new Date(person.issued_at).toLocaleDateString(
                                  undefined, { day: "numeric", month: "short",
                                               year: "numeric" })
                              : "Yes"}
                          </div>
                          <div className="text-xs text-muted tabular-nums">
                            {person.certificate}
                            {person.passed_on_attempt
                              ? person.passed_on_attempt > 1
                                ? ` · attempt ${person.passed_on_attempt}`
                                : " · first attempt"
                              : ""}
                          </div>
                        </>
                      ) : (
                        <span className="text-muted">No</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

function Figure({ label, value, strong }: {
  label: string
  value: number
  strong?: boolean
}) {
  return (
    <div className={`rounded-xl border bg-surface p-4 ${
      strong ? "border-accent" : "border-line"}`}>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-muted">{label}</div>
    </div>
  )
}
