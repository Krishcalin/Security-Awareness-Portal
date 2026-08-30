import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router"
import { Award, Clock, ListChecks, PlayCircle } from "lucide-react"

import { api, certificateUrl } from "../api/client"
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
    return {
      label: `${module.latest_score} of ${module.questions}` +
        (module.attempts > 1 ? ` · attempt ${module.attempts}` : ""),
      // Whether that is a pass is the server's answer, not a threshold
      // duplicated here where it can drift out of step with the one
      // that decides whether a certificate exists.
      tone: module.passed ? "text-right" : "text-wrong",
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

/** Set once a tab has already offered to resume, so "back to your
 *  training" reaches the list instead of bouncing straight back into the
 *  course. Per tab, so a fresh sign-in resumes again. */
const RESUMED = "resumed-this-session"

export function Home() {
  const [modules, setModules] = useState<ModuleSummary[] | null>(null)
  const [problem, setProblem] = useState("")
  const navigate = useNavigate()

  useEffect(() => {
    api.modules().then(setModules).catch((error) => setProblem(String(error.message)))
  }, [])

  // Somebody who signed out halfway through slide twelve is taken back to
  // slide twelve rather than having to find it. Only on the first landing
  // in a tab, or the list would be unreachable.
  useEffect(() => {
    let stale = false
    try {
      if (sessionStorage.getItem(RESUMED)) return
      sessionStorage.setItem(RESUMED, "1")
    } catch {
      // Private browsing, or storage blocked. Showing the list is the
      // safe outcome; nothing is lost but the convenience.
      return
    }
    api.resume().then(({ path }) => {
      if (!stale && path !== "/") navigate(path, { replace: true })
    }).catch(() => {
      // The list is already on screen; a failed resume is not worth an
      // error message.
    })
    return () => { stale = true }
  }, [navigate])

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
          const at = module.last_ordinal || module.furthest_ordinal
          const resumeAt = at > 1 ? `?slide=${at}` : ""
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
                      {/* Both numbers, because "drawn from 100" is the reason
                          a retake is not the same quiz over again. */}
                      {module.questions} questions
                      {module.bank > module.questions &&
                        `, drawn from ${module.bank}`}
                    </span>
                    <span className={status.tone}>{status.label}</span>
                  </div>
                </div>
                <div className="flex flex-col items-stretch gap-2">
                  <Link
                    to={`/module/${module.slug}${resumeAt}`}
                    className="inline-flex items-center justify-center gap-2
                               rounded-lg bg-accent px-4 py-2 text-sm
                               font-medium text-white hover:opacity-90"
                  >
                    <PlayCircle size={17} aria-hidden />
                    {module.furthest_ordinal > 0 ? "Resume" : "Start"}
                  </Link>
                  {module.certificate_serial && (
                    <a
                      href={certificateUrl(module.certificate_serial)}
                      className="inline-flex items-center justify-center gap-2
                                 rounded-lg border border-line px-4 py-2 text-sm"
                    >
                      <Award size={16} aria-hidden />
                      Certificate
                    </a>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
