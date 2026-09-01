import type {
  CheckpointReveal, CheckpointState,
  Learner, ModuleDetail, ModuleSummary, Report, ReportPerson, Result, Reveal,
  StartedAttempt,
} from "./types"

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

/** Where the browser is sent when the session has gone. */
export const SIGN_IN_PATH = "/auth/login"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  })
  if (response.status === 401) {
    // The session expired mid-course. Send them to sign in rather than
    // showing an error they cannot act on.
    window.location.href = SIGN_IN_PATH
    throw new ApiError(401, "not signed in")
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      // A non-JSON error body; the status text will have to do.
    }
    throw new ApiError(response.status, detail)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const api = {
  me: () => request<Learner>("/api/me"),
  modules: () => request<ModuleSummary[]>("/api/modules"),
  module: (slug: string) => request<ModuleDetail>(`/api/modules/${slug}`),

  recordProgress: (slug: string, furthest_ordinal: number) =>
    post<{ furthest_ordinal: number }>(
      `/api/modules/${slug}/progress`, { furthest_ordinal }),

  startAttempt: (slug: string) =>
    post<StartedAttempt>(`/api/modules/${slug}/attempts`),

  answer: (attemptId: number, ordinal: number, chosenIndex: number,
           tookMs: number) =>
    post<Reveal>(`/api/attempts/${attemptId}/responses`,
                 { ordinal, chosen_index: chosenIndex, took_ms: tookMs }),

  finish: (attemptId: number) =>
    post<Result>(`/api/attempts/${attemptId}/finish`),

  checkpoint: (slug: string, afterOrdinal: number) =>
    request<CheckpointState>(
      `/api/modules/${slug}/checkpoints/${afterOrdinal}`),

  answerCheckpoint: (slug: string, afterOrdinal: number, position: number,
                     chosenIndex: number, tookMs: number) =>
    post<CheckpointReveal>(
      `/api/modules/${slug}/checkpoints/${afterOrdinal}/answers`,
      { position, chosen_index: chosenIndex, took_ms: tookMs }),

  /** Where this person left off, decided on the server. */
  resume: () => request<{ path: string }>("/api/resume"),

  report: (slug: string) => request<Report>(`/api/report/${slug}`),
  reportPeople: (slug: string) =>
    request<ReportPerson[]>(`/api/report/${slug}/people`),
}

/** A plain link so the browser saves the file. */
export const reportExportUrl = (slug: string) =>
  `/api/report/${encodeURIComponent(slug)}/export.csv`

/** A plain link, so the browser downloads it rather than the app holding a
 *  700KB PDF in memory to hand back to the browser anyway. */
export const certificateUrl = (serial: string) =>
  `/api/certificates/${encodeURIComponent(serial)}`
