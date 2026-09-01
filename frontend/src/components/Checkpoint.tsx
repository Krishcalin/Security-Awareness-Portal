/**
 * Two questions every five slides.
 *
 * A narrated course can be left playing to an empty chair, and reaching the
 * last slide then proves only that the audio finished. This is the smallest
 * honest test that somebody is still there.
 *
 * IT TEACHES, IT DOES NOT MARK. A wrong answer shows the right one and the
 * explanation and then lets you past — the graded check at the end is where a
 * pass is earned. Six checkpoints that could each fail somebody would turn a
 * course into six exams, and the first thing anybody would do is stop
 * answering honestly. So there is no score here, no red cross on its own, and
 * nothing to retry: the explanation is the whole point of the exchange.
 */
import { useState } from "react"
import { Check, X } from "lucide-react"

import { api, ApiError } from "../api/client"
import type { CheckpointQuestion, CheckpointState } from "../api/types"

/** The slide a checkpoint follows, or null. Mirrors `checkpoints.points_for`
 *  in server/checkpoints.py — the server is the authority (it 404s a slide no
 *  checkpoint follows), and this only decides whether to ask. */
export const EVERY = 5

export function checkpointAfter(ordinal: number,
                                lessonCount: number): number | null {
  // Never after the last slide: a checkpoint there is the knowledge check.
  if (ordinal <= 0 || ordinal >= lessonCount) return null
  return ordinal % EVERY === 0 ? ordinal : null
}

export function Checkpoint({ slug, state, onComplete }: {
  slug: string
  state: CheckpointState
  onComplete: (next: CheckpointState) => void
}) {
  const [questions, setQuestions] = useState(state.questions)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState("")
  const [startedAt] = useState(() => Date.now())

  const answered = questions.filter((q) => q.answered).length
  const complete = answered === questions.length

  async function choose(question: CheckpointQuestion, index: number) {
    if (question.answered || busy) return
    setBusy(true)
    setProblem("")
    try {
      const reveal = await api.answerCheckpoint(
        slug, state.after_ordinal, question.position, index,
        Date.now() - startedAt)
      const next = questions.map((q) =>
        q.position === question.position ? { ...q, answered: reveal } : q)
      setQuestions(next)
      if (next.every((q) => q.answered)) {
        onComplete({ ...state, questions: next, complete: true })
      }
    } catch (failure) {
      // A 409 means this one was already answered — two tabs, or a double
      // click. Not an error worth alarming anybody about, but the panel has
      // to stop pretending the question is still open.
      setProblem(failure instanceof ApiError && failure.status === 409
        ? "That one was already answered. Reload to see the explanation."
        : "That did not save. Check your connection and try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      className="mt-6 rounded-xl border border-line bg-sunk p-5"
      aria-labelledby="checkpoint-heading"
    >
      <h2 id="checkpoint-heading" className="text-base font-semibold">
        Quick check
      </h2>
      <p className="mt-1 max-w-prose text-sm text-muted">
        {complete
          ? "That is it — carry on with the course."
          : `Two questions on what you have just heard. They are not marked and
             they do not affect your result; answer them and the course moves
             on.`}
      </p>

      {questions.map((question, n) => (
        <div key={question.position} className="mt-5">
          <p className="text-sm font-medium">
            <span className="text-muted">{n + 1}. </span>{question.prompt}
          </p>
          <ul className="mt-2 space-y-1.5">
            {question.options.map((option, index) => {
              const reveal = question.answered
              const isRight = reveal && index === reveal.correct_index
              const isTheirs = reveal && index === reveal.chosen_index
              return (
                <li key={index}>
                  <button
                    type="button"
                    disabled={Boolean(reveal) || busy}
                    onClick={() => void choose(question, index)}
                    className={[
                      "flex w-full items-start gap-2 rounded-lg border px-3 py-2",
                      "text-left text-sm transition-colors",
                      isRight ? "border-right bg-right-soft"
                        : isTheirs ? "border-wrong bg-wrong-soft"
                        : "border-line",
                      reveal ? "cursor-default" : "hover:border-accent",
                    ].join(" ")}
                  >
                    {/* The icon is on the RIGHT answer whether or not they
                        picked it. A tick that only appears when somebody was
                        correct leaves the person who was wrong looking at
                        four options and no answer. */}
                    <span className="mt-0.5 shrink-0">
                      {isRight ? <Check size={15} className="text-right" />
                        : isTheirs ? <X size={15} className="text-wrong" />
                        : <span className="inline-block w-[15px]" />}
                    </span>
                    <span>{option}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          {question.answered && (
            <p className="mt-2 rounded-lg bg-panel px-3 py-2 text-sm text-muted">
              {/* Shown right or wrong. Somebody who guessed correctly has
                  learned nothing from a tick. */}
              {question.answered.explains}
            </p>
          )}
        </div>
      ))}

      {problem && (
        <p className="mt-4 text-sm text-wrong" role="alert">{problem}</p>
      )}
    </section>
  )
}
