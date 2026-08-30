import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  Narrator, PAUSE, loadVoices, pauseSeconds, pickVoice, segment,
  speechSupported,
} from "./narration"
import { installFakeSpeech } from "./test/speech"

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

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
    expect(fake.spoken.map((u) => u.text)).toEqual(["Slide one.", "Slide two."])
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

    expect(fake.spoken.map((u) => u.text)).toEqual(["First sentence."])
    fake.spoken[0].onend()

    // Still silent: the gap has not elapsed.
    expect(fake.spoken).toHaveLength(1)
    vi.advanceTimersByTime(PAUSE.sentence)
    expect(fake.spoken.map((u) => u.text))
      .toEqual(["First sentence.", "Second sentence."])
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
    expect(fake.spoken.map((u) => u.text)).toEqual(["First.", "Second."])
  })

  it("follows the transcript through the whole slide, not each sentence", async () => {
    const n = await narrator()
    const onWord = vi.fn()
    const text = "First sentence. Second sentence."
    n.speak(text, { onWord })

    fake.spoken[0].onboundary({ name: "word", charIndex: 6 })
    expect(onWord).toHaveBeenLastCalledWith(6)

    fake.spoken[0].onend()
    vi.advanceTimersByTime(PAUSE.sentence)
    // "sentence" inside the SECOND utterance is at index 7 of that utterance,
    // and at 24 of the slide. The transcript needs the second number.
    fake.spoken[1].onboundary({ name: "word", charIndex: 7 })
    expect(onWord).toHaveBeenLastCalledWith(text.indexOf("Second") + 7)
  })
})
