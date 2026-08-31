import { Link } from "react-router"
import { Award, Download } from "lucide-react"

import { certificateUrl } from "../api/client"
import type { Completion } from "../api/types"

/**
 * What somebody sees where the course used to be, once they have passed it.
 *
 * The slides are not served to them any more — that is decided on the server,
 * not here — so this has to do more than say "no". It gives back the three
 * things they might have come for: what they scored, when, and the
 * certificate.
 */
export function Completed({ title, completed, cycle }:
                          { title: string; completed: Completion
                            cycle?: { name: string } | null }) {
  const on = new Date(completed.issued_at)
  return (
    <div className="mx-auto max-w-2xl px-5 py-12">
      <div className="rounded-xl border border-line bg-surface p-7
                      shadow-[var(--shadow)]">
        <Award size={30} className="text-right" aria-hidden />
        <h1 className="mt-3 text-xl font-semibold">
          You have completed this training
        </h1>
        <p className="mt-2 text-muted">
          {title}{cycle ? ` · ${cycle.name}` : ""}
        </p>

        <dl className="mt-6 grid gap-x-8 gap-y-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-wider text-muted">
              Passed on
            </dt>
            <dd className="mt-0.5 tabular-nums">
              <time dateTime={completed.issued_at}>
                {on.toLocaleDateString(undefined,
                  { day: "numeric", month: "long", year: "numeric" })}
              </time>
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wider text-muted">
              Score
            </dt>
            <dd className="mt-0.5 tabular-nums">
              {completed.score} of {completed.out_of}
              {/* Said here as well as in the report. A pass on the third go is
                  not the same evidence as a pass on the first, and the person
                  who earned the first one should see it. */}
              {completed.attempt_no === 1
                ? " · first attempt"
                : ` · attempt ${completed.attempt_no}`}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs uppercase tracking-wider text-muted">
              Certificate
            </dt>
            <dd className="mt-0.5 tabular-nums">{completed.serial}</dd>
          </div>
        </dl>

        <div className="mt-7 flex flex-wrap gap-3">
          <a
            href={certificateUrl(completed.serial)}
            className="inline-flex items-center gap-2 rounded-lg bg-accent
                       px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            <Download size={16} aria-hidden />
            Download your certificate
          </a>
          <Link
            to="/"
            className="inline-flex items-center rounded-lg border border-line
                       px-4 py-2 text-sm"
          >
            Back to your courses
          </Link>
        </div>

        <p className="mt-7 border-t border-line pt-5 text-sm text-muted">
          The slides and the knowledge check are closed now that you have
          passed. If you need to check something you learned here, ask the
          security team rather than guessing — that is what they are for, and
          it is the habit this training was about.
        </p>
      </div>
    </div>
  )
}
