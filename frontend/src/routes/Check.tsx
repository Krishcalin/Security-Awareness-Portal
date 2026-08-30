import { useEffect, useRef, useState } from "react"
import { Link, useParams } from "react-router"
import {
  Award, Check as Tick, CircleAlert, Download, Mail, X,
} from "lucide-react"

import { api, certificateUrl } from "../api/client"
import type { Result, Reveal, StartedAttempt } from "../api/types"

/**
 * The knowledge check.
 *
 * The explanation is shown after every answer, right or wrong. Being told only
 * "incorrect" wastes the one moment a learner is actually thinking about the
 * question, and this whole product exists to use that moment.
 *
 * Nothing here knows which answer is correct until the server says so. That is
 * not a matter of discipline in this file — the payload does not contain it.
 */
export function Check() {
  const { slug = "" } = useParams()
  const [attempt, setAttempt] = useState<StartedAttempt | null>(null)
  const [problem, setProblem] = useState("")
  const [position, setPosition] = useState(0)
  const [chosen, setChosen] = useState<number | null>(null)
  const [reveal, setReveal] = useState<Reveal | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [sending, setSending] = useState(false)
  const shownAt = useRef(Date.now())

  useEffect(() => {
    api.startAttempt(slug).then(setAttempt)
      .catch((error) => setProblem(String(error.message)))
  }, [slug])

  useEffect(() => {
    shownAt.current = Date.now()
    setChosen(null)
    setReveal(null)
  }, [position])

  if (problem) {
    return <p className="mx-auto max-w-2xl px-5 py-10 text-wrong">{problem}</p>
  }
  if (!attempt) {
    return <p className="mx-auto max-w-2xl px-5 py-10 text-muted">Loading…</p>
  }

  if (result) return <Outcome slug={slug} result={result} />

  const question = attempt.questions[position]
  if (!question) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        <p className="text-muted">
          Every question in this attempt has been answered.
        </p>
        <button
          type="button"
          onClick={() => api.finish(attempt.attempt_id).then(setResult)}
          className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          See your result
        </button>
      </div>
    )
  }

  const answered = attempt.answered + position
  const submit = async () => {
    if (chosen === null || sending) return
    setSending(true)
    try {
      setReveal(await api.answer(attempt.attempt_id, question.ordinal, chosen,
                                 Date.now() - shownAt.current))
    } catch (error) {
      setProblem(String((error as Error).message))
    } finally {
      setSending(false)
    }
  }

  const last = position === attempt.questions.length - 1
  const advance = async () => {
    if (last) setResult(await api.finish(attempt.attempt_id))
    else setPosition(position + 1)
  }

  return (
    <div className="mx-auto max-w-2xl px-5 py-8">
      <div className="flex items-baseline justify-between text-sm text-muted">
        <span className="tabular-nums">
          Question {answered + 1} of {attempt.out_of}
        </span>
        {attempt.attempt_no > 1 && <span>Attempt {attempt.attempt_no}</span>}
      </div>

      <h1 className="mt-3 text-xl font-semibold leading-snug tracking-tight">
        {question.prompt}
      </h1>

      <ul className="mt-5 grid gap-2">
        {question.options.map((option, i) => {
          const isChosen = chosen === i
          const isAnswer = reveal?.correct_index === i
          const wrongPick = reveal && isChosen && !reveal.correct
          return (
            <li key={i}>
              <button
                type="button"
                disabled={!!reveal}
                onClick={() => setChosen(i)}
                aria-pressed={isChosen}
                className={[
                  "flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-left",
                  reveal && isAnswer
                    ? "border-right bg-right-soft"
                    : wrongPick
                      ? "border-wrong bg-wrong-soft"
                      : isChosen
                        ? "border-accent bg-accent-soft"
                        : "border-line bg-surface hover:border-accent",
                ].join(" ")}
              >
                <span aria-hidden className="mt-0.5 shrink-0">
                  {reveal && isAnswer ? <Tick size={18} className="text-right" />
                    : wrongPick ? <X size={18} className="text-wrong" />
                    : <span className="inline-block h-[18px] w-[18px] rounded-full border border-current opacity-40" />}
                </span>
                <span>{option}</span>
              </button>
            </li>
          )
        })}
      </ul>

      {reveal ? (
        <div className="mt-5 rounded-xl border border-line bg-surface p-5">
          <p className={`font-semibold ${reveal.correct ? "text-right" : "text-wrong"}`}>
            {reveal.correct ? "Correct" : "Not quite"}
          </p>
          <p className="mt-2 leading-relaxed">{reveal.explains}</p>
          {reveal.teaches !== null && (
            <Link
              to={`/module/${slug}?slide=${reveal.teaches}`}
              className="mt-3 inline-block text-sm text-accent underline underline-offset-4"
            >
              Go back to slide {reveal.teaches}
            </Link>
          )}
          <button
            type="button"
            onClick={advance}
            className="mt-4 block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            {last ? "See your result" : "Next question"}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={submit}
          disabled={chosen === null || sending}
          className="mt-5 rounded-lg bg-accent px-4 py-2 text-sm font-medium
                     text-white disabled:opacity-40"
        >
          Submit answer
        </button>
      )}
    </div>
  )
}

/**
 * The result, said plainly.
 *
 * A pass on the third attempt is not the same evidence as a pass on the first,
 * so this does not print the same sentence for both. Unanswered questions are
 * named rather than quietly left out of the total.
 *
 * `passed` comes from the server. Recomputing it here from the score
 * would give a second answer to a question that already has one, and
 * the answer that decides whether a certificate exists is the one the
 * server gave.
 */
function Outcome({ slug, result }: { slug: string; result: Result }) {
  const passed = result.passed
  return (
    <div className="mx-auto max-w-2xl px-5 py-12">
      <p className="text-sm text-muted">
        {result.first_attempt ? "First attempt" : `Attempt ${result.attempt_no}`}
      </p>
      <h1 className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">
        {result.score} out of {result.out_of}
      </h1>

      <p className="mt-4 max-w-prose leading-relaxed">
        {passed && result.first_attempt &&
          "You answered these correctly the first time you saw them, which is the result worth having."}
        {passed && !result.first_attempt &&
          `You have this now. It took ${result.attempt_no} attempts, and that is recorded — not as a mark against you, but because a pass on the first go and a pass on the third are different pieces of evidence.`}
        {!passed &&
          `You needed ${result.needed} of ${result.out_of} to pass. Worth another look: the explanations you were given cover everything that was asked, and the slides are still there.`}
      </p>

      {result.certificate && (
        <div className="mt-6 rounded-xl border border-accent bg-accent-soft p-5">
          <h2 className="flex items-center gap-2 font-semibold">
            <Award size={19} aria-hidden />
            Your certificate is ready
          </h2>
          <p className="mt-2 text-sm">
            Issued to <strong>{result.certificate.name_printed}</strong>, from
            your name in the company directory. Reference{" "}
            <span className="tabular-nums">{result.certificate.serial}</span>.
          </p>
          <a
            href={certificateUrl(result.certificate.serial)}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent
                       px-4 py-2 text-sm font-medium text-white"
          >
            <Download size={16} aria-hidden />
            Download the PDF
          </a>
          {/* Said only when something is actually being posted. "Check your
              inbox", printed at somebody whose certificate was never sent,
              is worse than saying nothing about email at all. */}
          {result.certificate.will_email_to && (
            <p className="mt-3 flex items-start gap-2 text-sm">
              <Mail size={16} className="mt-0.5 shrink-0" aria-hidden />
              <span>
                A copy is on its way to {result.certificate.will_email_to} from
                the office of the CISO.
              </span>
            </p>
          )}
        </div>
      )}

      {result.unanswered > 0 && (
        <p className="mt-4 flex items-start gap-2 rounded-lg bg-sunk px-4 py-3 text-sm">
          <CircleAlert size={17} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            {result.unanswered} question{result.unanswered === 1 ? "" : "s"} went
            unanswered and counted as incorrect. Skipping what you are unsure of
            should not score better than trying it.
          </span>
        </p>
      )}

      <div className="mt-8 flex flex-wrap gap-3">
        <Link
          to={`/module/${slug}`}
          className="rounded-lg border border-line px-4 py-2 text-sm"
        >
          Review the slides
        </Link>
        <Link
          to="/"
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Back to your training
        </Link>
      </div>
    </div>
  )
}
