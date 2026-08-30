import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  LEAD_IN, Narrator, PAUSE, loadVoices, pauseSeconds, pickVoice, segment,
  speechSupported,
} from "./narration"
import { installFakeSpeech } from "./test/speech"

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

/** The words handed to the synthesiser, less the sacrificial lead-in. */
function said(fake: { spoken: { text: string }[] }): string[] {
  return fake.spoken.map((u) => u.text.slice(LEAD_IN.length))
}

describe("choosing a voice", () => {
  it("prefers a British voice for a British script", () => {
    const chosen = pickVoice([
      { name: "Samantha", lang: "en-US", localService: true },
      { name: "Daniel", lang: "en-GB", localService: true },
    ] as SpeechSynthesisVoice[])
    expect(chosen?.name).toBe("Daniel")
  })

  it("prefers a local voice over a cloud one, so the text stays on the machine", () => {
    const chosen = pickVoice([
      { name: "Cloud UK", lang: "en-GB", localService: false },
      { name: "Local UK", lang: "en-GB", localService: true },
    ] as SpeechSynthesisVoice[])
    expect(chosen?.name).toBe("Local UK")
  })

  it("falls back to any voice when none is English", () => {
    const chosen = pickVoice([
      { name: "Amelie", lang: "fr-FR", localService: true },
    ] as SpeechSynthesisVoice[])
    expect(chosen?.name).toBe("Amelie")
  })

  it("has nothing to choose from an empty list", () => {
    expect(pickVoice([])).toBeNull()
  })
})

describe("waiting for voices to load", () => {
  it("waits for voiceschanged when the first call returns nothing", async () => {
    const fake = installFakeSpeech([])
    const pending = loadVoices()
    fake.deliverVoices([{ name: "Daniel", lang: "en-GB" } as SpeechSynthesisVoice])
    expect((await pending).map((v) => v.name)).toEqual(["Daniel"])
  })

  it("gives up rather than hanging when no voices ever arrive", async () => {
    vi.useFakeTimers()
    installFakeSpeech([])
    const pending = loadVoices(500)
    await vi.advanceTimersByTimeAsync(600)
    expect(await pending).toEqual([])
  })
})

describe("a machine with no speech at all", () => {
  it("is reported rather than being silently silent", async () => {
    vi.stubGlobal("speechSynthesis", undefined)
    vi.stubGlobal("SpeechSynthesisUtterance", undefined)
    expect(speechSupported()).toBe(false)

    const narrator = new Narrator()
    expect(await narrator.prepare()).toBe(false)
    expect(narrator.currentStatus).toBe("unsupported")
  })

  it("is also reported when synthesis exists but has no voices installed", async () => {
    vi.useFakeTimers()
    installFakeSpeech([])
    const narrator = new Narrator()
    const pending = narrator.prepare()
    await vi.advanceTimersByTimeAsync(2_100)   // the wait-for-voices timeout
    expect(await pending).toBe(false)
    expect(narrator.currentStatus).toBe("unsupported")
  })
})

describe("speaking", () => {
  let fake: ReturnType<typeof installFakeSpeech>

  beforeEach(() => {
    fake = installFakeSpeech()
  })

  it("reports the end of the narration once", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    const onEnd = vi.fn()
    narrator.speak("Some narration.", { onEnd })
    fake.spoken[0].onend()
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it("ignores the end event of an utterance that was cancelled", async () => {
    // Browsers disagree about whether cancel() fires `end` on the utterance it
    // just cancelled. A player that advances on `end` skips a slide when it
    // does.
    const narrator = new Narrator()
    await narrator.prepare()
    const onEnd = vi.fn()
    narrator.speak("First slide.", { onEnd })
    narrator.stop()
    fake.spoken[0].onend()
    expect(onEnd).not.toHaveBeenCalled()
  })

  it("does not treat a deliberate interruption as the narration finishing", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    const onEnd = vi.fn()
    narrator.speak("First slide.", { onEnd })
    fake.spoken[0].onerror({ error: "interrupted" })
    expect(onEnd).not.toHaveBeenCalled()
  })

  it("does report a real synthesis failure, so the player is not stuck", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    const onEnd = vi.fn()
    narrator.speak("First slide.", { onEnd })
    fake.spoken[0].onerror({ error: "synthesis-failed" })
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it("replaces what is being said rather than talking over it", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    narrator.speak("Slide one.")
    narrator.speak("Slide two.")
    expect(said(fake)).toEqual(["Slide one.", "Slide two."])
    expect(fake.synth.speaking).toBe(true)
  })

  it("keeps Chrome speaking past its fifteen-second cut-off", async () => {
    vi.useFakeTimers()
    const narrator = new Narrator()
    await narrator.prepare()
    narrator.speak("A minute of narration.")
    fake.synth.speaking = true
    fake.synth.paused = false

    vi.advanceTimersByTime(35_000)
    expect(fake.synth.resumeCount).toBeGreaterThanOrEqual(3)
  })

  it("stops nudging once the narration is over", async () => {
    vi.useFakeTimers()
    const narrator = new Narrator()
    await narrator.prepare()
    narrator.speak("Short.")
    fake.spoken[0].onend()
    const after = fake.synth.resumeCount
    vi.advanceTimersByTime(60_000)
    expect(fake.synth.resumeCount).toBe(after)
  })

  it("treats an empty script as immediately finished", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    const onEnd = vi.fn()
    narrator.speak("   ", { onEnd })
    expect(onEnd).toHaveBeenCalledTimes(1)
    expect(fake.spoken).toHaveLength(0)
  })

  it("passes the chosen voice to the utterance", async () => {
    const narrator = new Narrator()
    await narrator.prepare()
    narrator.speak("Narrated.")
    expect(fake.spoken[0].voice.name).toBe("Daniel")
    expect(fake.spoken[0].lang).toBe("en-GB")
    expect(narrator.voiceName).toBe("Daniel")
  })
})


describe("splitting the narration for delivery", () => {
  it("keeps each sentence with its full stop", () => {
    const parts = segment("One thing. Then another. And a third.")
    expect(parts.map((p) => p.text)).toEqual([
      "One thing.", "Then another.", "And a third.",
    ])
  })

  it("waits after a full stop, which is the whole point", () => {
    const parts = segment("One thing. Then another.")
    expect(parts[0].pauseAfter).toBe(PAUSE.sentence)
  })

  it("waits longer between paragraphs than between sentences", () => {
    const parts = segment("End of one.\n\nStart of the next. And on.")
    expect(parts[0].pauseAfter).toBe(PAUSE.paragraph)
    expect(parts[1].pauseAfter).toBe(PAUSE.sentence)
  })

  it("takes a shorter breath after a colon", () => {
    const parts = segment("One: disconnect from the network. Two: report it.")
    expect(parts[0].text).toBe("One:")
    expect(parts[0].pauseAfter).toBe(PAUSE.clause)
    expect(PAUSE.clause).toBeLessThan(PAUSE.sentence)
  })

  it("treats a question the same as a statement", () => {
    const parts = segment("What does the padlock tell you? Not very much.")
    expect(parts[0].text).toBe("What does the padlock tell you?")
    expect(parts[0].pauseAfter).toBe(PAUSE.sentence)
  })

  it("does not drop a sentence nobody ended", () => {
    const parts = segment("A proper sentence. An unfinished one")
    expect(parts.map((p) => p.text)).toEqual([
      "A proper sentence.", "An unfinished one",
    ])
  })

  it("says nothing at all about nothing at all", () => {
    expect(segment("   ")).toEqual([])
  })

  it("reports where each piece came from, for the transcript", () => {
    const text = "One thing. Then another."
    for (const part of segment(text)) {
      expect(text.slice(part.start, part.start + part.text.length))
        .toBe(part.text)
    }
  })

  it("adds up the silence it will introduce", () => {
    // Three sentences, two gaps: the pause after the last one is never waited.
    const seconds = pauseSeconds("One. Two. Three.")
    expect(seconds).toBeCloseTo(PAUSE.sentence * 2 / 1000, 5)
  })
})

describe("speaking a whole slide", () => {
  let fake: ReturnType<typeof installFakeSpeech>

  beforeEach(() => {
    vi.useFakeTimers()
    fake = installFakeSpeech()
  })

  async function narrator() {
    const n = new Narrator()
    await n.prepare()
    return n
  }

  it("speaks one sentence, then waits, then speaks the next", async () => {
    const n = await narrator()
    n.speak("First sentence. Second sentence.")

    expect(said(fake)).toEqual(["First sentence."])
    fake.spoken[0].onend()

    // Still silent: the gap has not elapsed.
    expect(fake.spoken).toHaveLength(1)
    vi.advanceTimersByTime(PAUSE.sentence)
    expect(said(fake)).toEqual(["First sentence.", "Second sentence."])
  })

  it("does not report the end until the last sentence is said", async () => {
    const n = await narrator()
    const onEnd = vi.fn()
    n.speak("First. Second.", { onEnd })

    fake.spoken[0].onend()
    expect(onEnd).not.toHaveBeenCalled()
    vi.advanceTimersByTime(PAUSE.sentence)
    fake.spoken[1].onend()
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it("stops mid-slide without the next sentence arriving anyway", async () => {
    const n = await narrator()
    n.speak("First. Second.")
    fake.spoken[0].onend()
    n.stop()
    vi.advanceTimersByTime(PAUSE.sentence * 10)
    expect(fake.spoken).toHaveLength(1)
  })

  it("can be paused during the silence between two sentences", async () => {
    // There is nothing for the synthesiser to pause at that moment, so the
    // next sentence has to be held rather than allowed to start.
    const n = await narrator()
    n.speak("First. Second.")
    fake.spoken[0].onend()
    n.pause()
    expect(n.currentStatus).toBe("paused")

    vi.advanceTimersByTime(PAUSE.sentence * 10)
    expect(fake.spoken).toHaveLength(1)

    n.resume()
    expect(said(fake)).toEqual(["First.", "Second."])
  })

  it("follows the transcript through the whole slide, not each sentence", async () => {
    const n = await narrator()
    const onWord = vi.fn()
    const text = "First sentence. Second sentence."
    n.speak(text, { onWord })

    // charIndex counts from the start of the utterance, lead-in included.
    fake.spoken[0].onboundary({ name: "word", charIndex: LEAD_IN.length + 6 })
    expect(onWord).toHaveBeenLastCalledWith(6)

    fake.spoken[0].onend()
    vi.advanceTimersByTime(PAUSE.sentence)
    // "sentence" is at index 7 of the second sentence, and at 24 of the slide.
    // The transcript needs the second number.
    fake.spoken[1].onboundary({ name: "word", charIndex: LEAD_IN.length + 7 })
    expect(onWord).toHaveBeenLastCalledWith(text.indexOf("Second") + 7)
  })
})

describe("the lead-in that gets clipped instead of a word", () => {
  let fake: ReturnType<typeof installFakeSpeech>

  beforeEach(() => {
    vi.useFakeTimers()
    fake = installFakeSpeech()
  })

  async function narrator() {
    const n = new Narrator()
    await n.prepare()
    return n
  }

  it("puts it in front of every sentence, not only the first", async () => {
    // Chrome clips the opening of an UTTERANCE. One utterance per sentence
    // means one clip per sentence, which is the original complaint moved.
    const n = await narrator()
    n.speak("First. Second. Third.")
    fake.spoken[0].onend()
    vi.advanceTimersByTime(PAUSE.sentence)
    fake.spoken[1].onend()
    vi.advanceTimersByTime(PAUSE.sentence)

    expect(fake.spoken).toHaveLength(3)
    for (const utterance of fake.spoken) {
      expect(utterance.text.startsWith(LEAD_IN)).toBe(true)
    }
  })

  it("is punctuation, so it is never read out as a word", () => {
    expect(LEAD_IN).not.toMatch(/[A-Za-z0-9]/)
    expect(LEAD_IN.length).toBeGreaterThan(0)
  })

  it("does not shift the transcript", async () => {
    const n = await narrator()
    const onWord = vi.fn()
    n.speak("Alpha beta gamma.", { onWord })
    // "beta" is at 6 in the script and at 6 + lead-in in the utterance.
    fake.spoken[0].onboundary({ name: "word", charIndex: LEAD_IN.length + 6 })
    expect(onWord).toHaveBeenLastCalledWith(6)
  })

  it("never reports a position before the sentence began", async () => {
    // A boundary event landing inside the lead-in itself.
    const n = await narrator()
    const onWord = vi.fn()
    n.speak("First. Second.", { onWord })
    fake.spoken[0].onend()
    vi.advanceTimersByTime(PAUSE.sentence)
    fake.spoken[1].onboundary({ name: "word", charIndex: 0 })
    expect(onWord).toHaveBeenLastCalledWith("First. Second.".indexOf("Second"))
  })
})

describe("a sentence too long for one utterance", () => {
  const LONG =
    "During the 2015 Ukraine grid attack, at the same moment they were opening " +
    "breakers, they launched a flood of automated calls at the utility's " +
    "customer service centre, jamming the lines so customers could not report " +
    "the outage and operators could not work out what was happening."

  it("is broken up, because Chrome cuts one off part way through", () => {
    // Forty-five words is about eighteen seconds. Chrome stops at fifteen,
    // with no error and no `end` event — so the player waits for a slide that
    // has already stopped talking.
    const parts = segment(LONG)
    expect(parts.length).toBeGreaterThan(1)
    for (const part of parts) {
      expect(part.text.split(/\s+/).length).toBeLessThanOrEqual(30)
    }
  })

  it("is broken only where the author already put punctuation", () => {
    for (const part of segment(LONG)) {
      // Each piece ends at a comma, a dash or the full stop — never mid-clause.
      expect(part.text).toMatch(/[,;–—.]$/)
    }
  })

  it("still adds back up to the sentence", () => {
    expect(segment(LONG).map((p) => p.text).join(" ").split(/\s+/))
      .toEqual(LONG.split(/\s+/))
  })

  it("reports where each piece really starts, for the transcript", () => {
    for (const part of segment(LONG)) {
      expect(LONG.slice(part.start, part.start + part.text.length))
        .toBe(part.text)
    }
  })

  it("breathes at the breaks rather than stopping", () => {
    const parts = segment(LONG)
    expect(parts[0].pauseAfter).toBe(PAUSE.clause)
    expect(parts[parts.length - 1].pauseAfter).toBe(PAUSE.sentence)
  })

  it("leaves a long sentence with nowhere to break alone", () => {
    // Cutting at an arbitrary word would sound worse than a sentence that
    // trails off, and the build refuses to ship one anyway.
    const unbroken = Array(40).fill("word").join(" ") + "."
    expect(segment(unbroken)).toHaveLength(1)
  })

  it("leaves an ordinary sentence exactly as it was", () => {
    const parts = segment("A short sentence, with a comma in it. And another.")
    expect(parts.map((p) => p.text)).toEqual([
      "A short sentence, with a comma in it.", "And another.",
    ])
  })
})
