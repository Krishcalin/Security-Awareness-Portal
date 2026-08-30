/**
 * The splitter against the real script, not against toy strings.
 *
 * Every other test here feeds it sentences written to exercise it. This one
 * feeds it the twenty slides people will actually hear, which is where a
 * splitting rule that looked fine in isolation turns out to cut a sentence in
 * half or swallow one whole.
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

import { PAUSE, segment } from "./narration"

// Relative to the working directory (frontend/) rather than to import.meta,
// which under jsdom is not a file URL.
const course = JSON.parse(readFileSync(
  resolve(process.cwd(), "../data/modules/security-awareness-essentials.json"),
  "utf-8",
))

interface Lesson { ordinal: number; narration: string }

const narrated: Lesson[] = course.lessons.filter((l: Lesson) => l.narration)

describe("the authored narration", () => {
  it("has slides to say something about", () => {
    // Thirty-one slides, thirty narrated: the quiz gate is deliberately silent.
    expect(course.lessons.length).toBe(31)
    expect(narrated.length).toBe(30)
  })

  // One test per slide, so a failure names the slide rather than the loop.
  for (const lesson of narrated) {
    it(`slide ${lesson.ordinal} splits into pieces that add back up to the script`, () => {
      const parts = segment(lesson.narration)

      // Nothing invented and nothing lost: every piece is the script at the
      // offset it claims, and the pieces cover all of its words.
      for (const part of parts) {
        expect(lesson.narration.slice(part.start, part.start + part.text.length))
          .toBe(part.text)
      }
      expect(parts.map((p) => p.text).join(" ").split(/\s+/))
        .toEqual(lesson.narration.split(/\s+/).filter(Boolean))
    })
  }

  it("breaks every slide into more than one piece", () => {
    // A slide spoken as one utterance is a slide with no pauses in it, which
    // is the bug this whole mechanism exists to fix.
    for (const lesson of narrated) {
      expect(segment(lesson.narration).length).toBeGreaterThan(5)
    }
  })

  it("never leaves a piece long enough for Chrome to cut it off", () => {
    // Chrome stops speaking after about fifteen seconds. At 150 words per
    // minute that is roughly 37 words.
    for (const lesson of narrated) {
      for (const part of segment(lesson.narration)) {
        expect(part.text.split(/\s+/).length).toBeLessThan(37)
      }
    }
  })

  it("never produces a piece that is only punctuation", () => {
    for (const lesson of narrated) {
      for (const part of segment(lesson.narration)) {
        expect(part.text).toMatch(/[A-Za-z]/)
      }
    }
  })

  it("pauses longest where the script changes paragraph", () => {
    const withParagraphs = narrated.filter(
      (l: { narration: string }) => /\n\s*\n/.test(l.narration))
    expect(withParagraphs.length).toBeGreaterThan(0)
    for (const lesson of withParagraphs) {
      expect(segment(lesson.narration).map((p) => p.pauseAfter))
        .toContain(PAUSE.paragraph)
    }
  })
})
