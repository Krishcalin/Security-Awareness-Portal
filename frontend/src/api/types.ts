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
  attempts: number | null
  latest_score: number | null
}

export interface Lesson {
  ordinal: number
  title: string
  body: string
  animation: string
  image: string
  narration: string
  audio_url: string
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

export interface Result {
  attempt_no: number
  score: number
  out_of: number
  unanswered: number
  first_attempt: boolean
}

export interface Learner {
  id: number
  email: string
  display_name: string
  department: string
}
