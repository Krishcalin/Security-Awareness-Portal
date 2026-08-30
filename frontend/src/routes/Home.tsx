import { useEffect, useState } from "react"
import { Link } from "react-router"
import { Clock, ListChecks, PlayCircle } from "lucide-react"

import { api } from "../api/client"
import type { ModuleSummary } from "../api/types"

/**
 * What to say about where somebody has got to.
 *
 * "Not completed" is the phrase this deliberately avoids for the middle case.
 * Somebody who opened the course and stopped on slide three has told you
 * something — the material lost them, or it was too long, or it went in at
 * the wrong moment — and lumping them in with the people who never opened it
 * throws that away. The two are separate lines here.
 */
function standing(module: ModuleSummary): { label: string; tone: string } {
  if (module.latest_score !== null && module.attempts) {
    const passed = module.latest_score >= Math.ceil(module.questions * 0.8)
    return {
      label: `${module.latest_score} of ${module.questions}` +
        (module.attempts > 1 ? ` · attempt ${module.attempts}` : ""),
      tone: passed ? "text-right" : "text-wrong",
    }
  }
  if (module.completed_at) return { label: "Reached the end", tone: "text-muted" }
  if (module.furthest_ordinal > 0) {
    return {
      label: `Stopped on slide ${module.furthest_ordinal} of ${module.lessons}`,
      tone: "text-muted",
    }
  }
  return { label: "Not started", tone: "text-muted" }
}

export function Home() {
  const [modules, setModules] = useState<ModuleSummary[] | null>(null)
  const [problem, setProblem] = useState("")

  useEffect(() => {
    api.modules().then(setModules).catch((error) => setProblem(String(error.message)))
  }, [])

  if (problem) {
    return <p className="mx-auto max-w-5xl px-5 py-10 text-wrong">{problem}</p>
  }
  if (!modules) {
    return <p className="mx-auto max-w-5xl px-5 py-10 text-muted">Loading…</p>
  }

  return (
    <div className="mx-auto max-w-5xl px-5 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Your training</h1>
      <p className="mt-1 text-muted">
        Each module is narrated. You can read along, and everything is
        answerable from the keyboard.
      </p>

      <ul className="mt-8 grid gap-4">
        {modules.map((module) => {
          const status = standing(module)
          const resumeAt = module.furthest_ordinal > 1
            ? `?slide=${module.furthest_ordinal}` : ""
          return (
            <li
              key={module.slug}
              className="rounded-xl border border-line bg-surface p-5 shadow-[var(--shadow)]"
            >
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-1">
                  <h2 className="text-lg font-semibold">{module.title}</h2>
                  <p className="mt-1 max-w-prose text-sm text-muted">
                    {module.summary}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted">
                    <span className="inline-flex items-center gap-1.5">
                      <Clock size={15} aria-hidden /> {module.minutes} min
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <ListChecks size={15} aria-hidden />
                      {module.questions} questions
                    </span>
                    <span className={status.tone}>{status.label}</span>
                  </div>
                </div>
                <Link
                  to={`/module/${module.slug}${resumeAt}`}
                  className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2
                             text-sm font-medium text-white hover:opacity-90"
                >
                  <PlayCircle size={17} aria-hidden />
                  {module.furthest_ordinal > 0 ? "Resume" : "Start"}
                </Link>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
