/**
 * Dates, formatted for reading.
 *
 * Here rather than beside the API client because these are pure functions
 * with nothing to do with fetching: a screen test that mocks the client would
 * otherwise have to hand-write a date formatter to get a page to render at
 * all, which is a mock of the thing under test.
 */
/**
 * A cycle's deadline, as the date it was entered as.
 *
 * `due_at` is the last instant of a day in UTC, so anywhere east of it a
 * plain toLocaleDateString rolls the date forward: a deadline typed as
 * 31 March renders as 1 April in Kolkata. For a compliance date that is not a
 * cosmetic difference — somebody told the first who submits on the first is
 * late — so it is read back in the zone it was written in.
 *
 * The other dates on these screens are instants and are rightly shown in the
 * reader's own zone: when somebody passed is a moment, not a date on a form.
 */
export function dueDate(when: string): string {
  return new Date(when).toLocaleDateString(undefined, {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  })
}

export function shortDueDate(when: string): string {
  return new Date(when).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  })
}
