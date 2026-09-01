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
  /** Being shown a course this person has already passed, because their
   *  address is listed in CONTENT_REVIEWERS. The slides come back; the
   *  knowledge check does not. */
  reviewing: boolean
  certificate_serial: string | null
  cycle_name: string | null
  cycle_due_at: string | null
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

/**
 * A successful completion, as it was recorded at the time.
 *
 * This IS the certificate row rather than a copy of it: the attempt that
 * earned it, the score as it stood, and the date. The pass mark is a setting
 * that can change, so a figure worked out fresh each time would quietly
 * un-pass people the day somebody moved the threshold.
 */
export interface Completion {
  serial: string
  issued_at: string
  score: number
  out_of: number
  name_printed: string
  attempt_no: number
}

/** A period the training has to be completed within. Null on a portal where
 *  nobody has opened one, which behaves as it always did: passing closes the
 *  course for good. */
export interface Cycle {
  id: number
  name: string
  opens_at: string
  due_at: string | null
}

/** A certificate from an earlier period, for somebody who is due again. */
export interface EarlierPass {
  serial: string
  issued_at: string
  cycle_name: string | null
}

export interface ModuleDetail {
  slug: string
  title: string
  summary: string
  minutes: number
  content_hash: string
  /** Empty once `completed` is set: the server stops serving the deck to
   *  somebody who has passed, rather than trusting the browser to hide it. */
  lessons: Lesson[]
  completed: Completion | null
  /** See ModuleSummary above. When this is set, `completed` is too — and the
   *  slides are served anyway. */
  reviewing: boolean
  cycle: Cycle | null
  /** Set only when they passed a PREVIOUS cycle and this one is outstanding. */
  previously: EarlierPass | null
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

/** server/roster.py — who was supposed to take the course.
 *
 *  The report used to divide by `count(*) FROM learner`, and a learner row
 *  exists only once somebody has SIGNED IN. So a person who ignored the
 *  training was in neither the numerator nor the denominator. `null` on the
 *  summary means no roster has been imported, which is a different state from
 *  a roster naming nobody — and the screen says which. */
export interface RosterStats {
  expected: number
  /** The figure the old report could not produce at all. Not the same as
   *  `signed_in_not_started`: one needs an account or a nudge, the other needs
   *  reminding to open it. */
  never_signed_in: number
  signed_in_not_started: number
  started_not_passed: number
  passed: number
  /** People with results who are on no roster row — the other half of every
   *  matching failure. A changed email address appears once here and once in
   *  `never_signed_in_people`. */
  off_roster: number
  never_signed_in_people: {
    email: string; display_name: string; department: string
  }[]
}

export interface ReportPerson {
  /** Null for somebody on the roster who has never signed in: there is no
   *  learner row, because nothing has ever created one for them. */
  id: number | null
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
  /** False for a roster entry that matched no learner. Present only when a
   *  roster exists; without one every row is somebody who signed in. */
  signed_in?: boolean
  on_roster?: boolean
}

export interface Report {
  module: { slug: string; title: string; content_hash: string }
  /** Six numbers, never one. See server/reporting.py. */
  summary: {
    /** The period every figure below is about. Null on a portal where nobody
     *  has opened a cycle, where they are about all of time. */
    cycle: Cycle | null
    overdue: boolean
    /** Null until somebody imports a roster. The screen must then say that
     *  `people` counts sign-ins rather than the workforce. */
    roster: RosterStats | null
    /** Everybody who has ever signed in — NOT everybody who was supposed to
     *  take the course. Kept beside the roster figures because "signed in and
     *  never opened it" is a real state with its own remedy. */
    people: number
    never_opened: number
    opened: number
    stopped_partway: number
    reached_end: number
    passed: number
    passed_first_time: number
  }
  cycles: (Cycle & { passed: number; open: boolean })[]
  questions: ReportQuestion[]
  slides: ReportSlide[]
  departments: { department: string; people: number; reached_end: number; passed: number }[]
  off_roster: {
    learner_id: number; email: string; display_name: string
    department: string; started_at: string | null
    completed_at: string | null; certificate: string | null
  }[]
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
