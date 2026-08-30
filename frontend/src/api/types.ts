/**
 * The shapes the API actually returns.
 *
 * `Question` has no `correct_index` and no `explains`, and that is not an
 * oversight to be tidied up later: the server does not send them before the
 * question is answered, and this type says so. If a future change makes the
 * answer available here, the compiler should be the thing that objects.
 */

export interface ModuleSummary {
  slug: string
  title: string
  summary: string
  minutes: number
  topic: string
  content_hash: string
  lessons: number
  questions: number
  started_at: string | null
  completed_at: string | null
  furthest_ordinal: number
  /** Where they actually were when they stopped, which is not the same
   *  as how far they got: somebody who went back to re-read slide 4
   *  resumes at 4, and their progress is still 18. */
  last_ordinal: number
  attempts: number | null
  latest_score: number | null
  /** Decided on the server, against the same threshold that issues the
   *  certificate. The browser is not asked to work it out from a score,
   *  because then there are two answers to the same question. */
  passed: boolean
  certificate_serial: string | null
  /** How many are in the bank the ten are drawn from. `questions` above is
   *  what a learner actually answers. */
  bank: number
}

export interface Lesson {
  ordinal: number
  title: string
  body: string
  animation: string
  image: string
  narration: string
  audio_url: string
  /** Where each word falls in the recording. Empty when there is no
   *  recording, or when the script has moved on since it was made. */
  audio_timings_url: string
  narration_seconds: number
}

export interface Enrolment {
  started_at: string | null
  completed_at: string | null
  furthest_ordinal: number
}

export interface ModuleDetail {
  slug: string
  title: string
  summary: string
  minutes: number
  content_hash: string
  lessons: Lesson[]
  question_count: number
  question_bank: number
  enrolment: Enrolment | null
}

/** A question as a learner may see it: no answer, by construction. */
export interface Question {
  ordinal: number
  prompt: string
  options: string[]
}

export interface StartedAttempt {
  attempt_id: number
  attempt_no: number
  out_of: number
  answered: number
  questions: Question[]
}

/** What comes back after answering — including, at last, the answer. */
export interface Reveal {
  ordinal: number
  correct: boolean
  correct_index: number
  explains: string
  teaches: number | null
}

export interface Certificate {
  serial: string
  name_printed: string
  issued_at: string
  /** The address the portal will send to, or empty when it will not send at
   *  all. Empty means say nothing about email, rather than "check your
   *  inbox" for a message that was never posted. */
  will_email_to: string
}

export interface Result {
  attempt_no: number
  score: number
  out_of: number
  unanswered: number
  first_attempt: boolean
  passed: boolean
  pass_mark: number
  /** How many were needed to pass, so a near miss can say so precisely. */
  needed: number
  certificate: Certificate | null
}

export interface Learner {
  id: number
  email: string
  display_name: string
  department: string
  /** Decides whether the reporting link is drawn. It is NOT what authorises
   *  the reports — every one of those endpoints checks for itself. */
  role: "learner" | "admin"
}

export interface ReportQuestion {
  question_id: number
  ordinal: number
  prompt: string
  teaches: number | null
  teaches_title: string | null
  answered: number
  correct: number
  /** Null below the floor where a proportion would be noise. */
  correct_rate: number | null
  guessed: number
  verdict: string
}

export interface ReportSlide {
  ordinal: number
  title: string
  stopped_here: number
  reached: number
}

export interface ReportPerson {
  id: number
  email: string
  display_name: string
  department: string
  started_at: string | null
  completed_at: string | null
  furthest_ordinal: number
  attempts: number | null
  latest_score: number | null
  out_of: number | null
  certificate: string | null
  issued_at: string | null
  passed_on_attempt: number | null
}

export interface Report {
  module: { slug: string; title: string; content_hash: string }
  /** Six numbers, never one. See server/reporting.py. */
  summary: {
    people: number
    never_opened: number
    opened: number
    stopped_partway: number
    reached_end: number
    passed: number
    passed_first_time: number
  }
  questions: ReportQuestion[]
  slides: ReportSlide[]
  departments: { department: string; people: number; reached_end: number; passed: number }[]
  delivery: {
    issued: number
    emailed: number
    failed: number
    not_attempted: number
    failures: { serial: string; email: string; email_error: string }[]
  }
  thresholds: {
    min_answers: number
    not_discriminating: number
    material_failed: number
    pass_mark: number
  }
}
