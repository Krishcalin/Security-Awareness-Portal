/**
 * Speaking the narration.
 *
 * The browser's speech synthesis is the right tool here — nothing to host, no
 * audio files to keep in step with a script that gets edited, and the words
 * stay reviewable as text. It is also an API with several ways of failing that
 * look like nothing at all, and every one of them has been hit by somebody
 * shipping a page like this:
 *
 *   - `getVoices()` returns an empty array on first call in most browsers.
 *     Voices arrive later, on a `voiceschanged` event. Code that picks a voice
 *     synchronously on load silently gets the default one for the OS locale,
 *     which for a British script is usually an American voice.
 *
 *   - Chrome stops speaking after roughly fifteen seconds. Not an error, not
 *     an `end` event — it simply stops. The keep-alive below nudges it; since
 *     the text is now spoken a sentence at a time, few utterances get near
 *     that limit anyway, but a long sentence still can.
 *
 *   - `cancel()` sometimes fires `end` on the utterance it just cancelled and
 *     sometimes does not. A player that advances on `end` will skip a slide.
 *     Every callback here is stamped with the utterance it belongs to.
 *
 *   - Speech will not start without a user gesture. The first play must come
 *     from a click, which is why nothing here auto-starts on mount.
 *
 * And the case that is not a bug: some browsers have no voices installed at
 * all. Then this reports `unsupported` and the player shows the script as
 * text. A course that is silently silent teaches nobody.
 *
 * WHY THE TEXT IS SPOKEN A SENTENCE AT A TIME. Handed a whole slide, the
 * engines run sentences together: the full stop gets no more silence than a
 * comma, and the first word of the next sentence lands before the listener has
 * finished the last one — so it is heard as a stumble, or not heard at all.
 * There is no markup that fixes this. SSML `<break>` is ignored by the Web
 * Speech API, and padding the text with extra spaces or commas either does
 * nothing or gets read out. The one thing that reliably produces silence is
 * silence: split the text on its own punctuation, speak each part as its own
 * utterance, and wait between them.
 */

export type NarratorStatus = "idle" | "speaking" | "paused" | "unsupported"

export interface SpeakOptions {
  onEnd?: () => void
  onWord?: (charIndex: number) => void
  onStatus?: (status: NarratorStatus) => void
}

/**
 * How long to be quiet after each kind of break, in milliseconds.
 *
 * These are duplicated in `tools/build_content.py`, which needs them to say
 * how long a slide takes; a test fails if the two sets drift apart.
 */
export const PAUSE = {
  /** After a full stop, question mark or exclamation mark. */
  sentence: 350,
  /** After a colon or semicolon — a shorter breath, not a full stop. */
  clause: 200,
  /** Between paragraphs, where the script itself changes subject. */
  paragraph: 650,
}

/**
 * Spoken before every sentence, to be swallowed instead of the first word.
 *
 * Chrome on Windows clips the opening of an utterance — the audio device is
 * still starting when the synthesiser begins, and roughly the first tenth of a
 * second is lost. On one utterance per slide that costs the first word of the
 * slide; on one utterance per sentence it costs the first word of every
 * sentence, which is the same complaint in a new place.
 *
 * A leading comma gives the clip something to eat. It is not read aloud, it
 * makes a short prosodic pause, and if the browser does not clip it the pause
 * is welcome anyway. Every character of it is subtracted back out of the
 * word-boundary offsets, so the transcript is unaffected.
 */
export const LEAD_IN = ", "

/** Chrome's ~15s cut-off; nudged well inside it. */
const KEEPALIVE_MS = 10_000

/** How long to wait for voices before deciding there are none. */
const VOICE_TIMEOUT_MS = 2_000

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance === "function"
}

/** One spoken piece: what to say, where it came from, and the silence after. */
export interface Segment {
  text: string
  /** Offset in the original narration, so the transcript can follow along. */
  start: number
  pauseAfter: number
}

/**
 * Split narration into the pieces to speak, with the silence between them.
 *
 * Deliberately simple, because the material it runs on is: British prose with
 * no abbreviations, no decimals and no ellipses. If any of those ever appear
 * this would split "e.g." into two sentences, so the guard is a test over the
 * authored script rather than cleverness here.
 */
export function segment(text: string): Segment[] {
  const segments: Segment[] = []
  const boundary = /([.!?]+|[:;])(\s+|$)/g
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = boundary.exec(text)) !== null) {
    const end = match.index + match[1].length
    const spoken = text.slice(cursor, end).trim()
    if (spoken) {
      const gap = match[2]
      segments.push({
        text: spoken,
        start: text.indexOf(spoken, cursor),
        pauseAfter: /\n\s*\n/.test(gap) ? PAUSE.paragraph
          : /[.!?]/.test(match[1]) ? PAUSE.sentence
            : PAUSE.clause,
      })
    }
    cursor = end + match[2].length
  }

  // Anything after the last full stop — a heading, or a sentence somebody
  // forgot to end. Spoken rather than dropped.
  const tail = text.slice(cursor).trim()
  if (tail) {
    segments.push({ text: tail, start: text.indexOf(tail, cursor),
                    pauseAfter: 0 })
  }
  return segments
}

/** Roughly how long the pauses add to a piece of narration, in seconds. */
export function pauseSeconds(text: string): number {
  const segments = segment(text)
  // The last pause is not waited out — the narration is over.
  return segments.slice(0, -1)
    .reduce((total, s) => total + s.pauseAfter, 0) / 1000
}

/**
 * The best available voice for a British English script.
 *
 * Preference order, and the reason for it: an en-GB voice reads "organisation"
 * and "colleague" without the mismatch between an American voice and British
 * spelling; a local voice does not send the text of the course to a cloud
 * service on every slide.
 */
export function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  if (!voices.length) return null
  const english = voices.filter((v) => v.lang?.toLowerCase().startsWith("en"))
  if (!english.length) return voices[0]
  const score = (v: SpeechSynthesisVoice) =>
    (v.lang?.toLowerCase().startsWith("en-gb") ? 2 : 0) +
    (v.localService ? 1 : 0)
  return [...english].sort((a, b) => score(b) - score(a))[0]
}

/**
 * The voices, once the browser has actually loaded them.
 *
 * Resolves with an empty list rather than hanging if none ever arrive, so a
 * browser without speech shows the transcript instead of a spinner.
 */
export function loadVoices(timeoutMs = VOICE_TIMEOUT_MS): Promise<SpeechSynthesisVoice[]> {
  if (!speechSupported()) return Promise.resolve([])
  const existing = window.speechSynthesis.getVoices()
  if (existing.length) return Promise.resolve(existing)

  return new Promise((resolve) => {
    let settled = false
    const done = () => {
      if (settled) return
      settled = true
      window.speechSynthesis.removeEventListener("voiceschanged", done)
      resolve(window.speechSynthesis.getVoices())
    }
    window.speechSynthesis.addEventListener("voiceschanged", done)
    window.setTimeout(done, timeoutMs)
  })
}

export class Narrator {
  private voice: SpeechSynthesisVoice | null = null
  private keepAlive: number | null = null
  /** Stamped on every utterance so a callback from a cancelled one is ignored. */
  private token = 0
  private status: NarratorStatus = "idle"
  private notify: ((status: NarratorStatus) => void) | undefined

  private queue: Segment[] = []
  private at = 0
  private options: SpeakOptions = {}
  /** The timer counting out the silence between two sentences. */
  private gap: number | null = null
  /** Set when a pause lands during that silence rather than during speech. */
  private waitingToResume = false

  async prepare(): Promise<boolean> {
    if (!speechSupported()) {
      this.setStatus("unsupported")
      return false
    }
    const voices = await loadVoices()
    this.voice = pickVoice(voices)
    if (!this.voice) {
      // Speech synthesis exists but the machine has no voices installed. This
      // happens on stripped-down Linux images and some locked-down builds.
      this.setStatus("unsupported")
      return false
    }
    return true
  }

  get voiceName(): string {
    return this.voice?.name ?? ""
  }

  get currentStatus(): NarratorStatus {
    return this.status
  }

  private setStatus(status: NarratorStatus) {
    this.status = status
    this.notify?.(status)
  }

  /**
   * Speak, replacing whatever is being said. Must be called from a user
   * gesture the first time, or the browser will refuse and say nothing.
   */
  speak(text: string, options: SpeakOptions = {}): void {
    if (!speechSupported() || !text.trim()) {
      options.onEnd?.()
      return
    }
    this.stop()
    this.notify = options.onStatus
    this.options = options
    this.queue = segment(text)
    this.at = 0
    if (!this.queue.length) {
      options.onEnd?.()
      return
    }
    this.setStatus("speaking")
    this.say(++this.token)
  }

  /** Speak the segment at `this.at`, and arrange for the next one. */
  private say(mine: number): void {
    if (mine !== this.token) return
    const piece = this.queue[this.at]
    if (!piece) {
      this.finish(mine)
      return
    }

    const utterance = new SpeechSynthesisUtterance(LEAD_IN + piece.text)
    if (this.voice) {
      utterance.voice = this.voice
      utterance.lang = this.voice.lang
    }
    utterance.rate = 1
    utterance.pitch = 1

    utterance.onend = () => {
      if (mine !== this.token) return   // a cancelled utterance reporting in
      this.stopKeepAlive()
      this.at += 1
      if (this.at >= this.queue.length) {
        this.finish(mine)
        return
      }
      // The silence the whole change is for. Without it the next sentence
      // begins on top of this one and its first word is lost.
      this.gap = window.setTimeout(() => {
        this.gap = null
        this.say(mine)
      }, piece.pauseAfter)
    }

    utterance.onerror = (event) => {
      if (mine !== this.token) return
      this.stopKeepAlive()
      // "interrupted" and "canceled" are what a deliberate stop looks like;
      // they are not failures and must not advance the slide.
      const reason = (event as SpeechSynthesisErrorEvent).error
      if (reason === "interrupted" || reason === "canceled") {
        this.setStatus("idle")
        return
      }
      this.finish(mine)
    }

    if (this.options.onWord) {
      utterance.onboundary = (event) => {
        if (mine !== this.token) return
        if (event.name === "word" || event.name === undefined) {
          // Offset into the whole narration, not into this sentence, or the
          // transcript would highlight from the top on every full stop — and
          // less the lead-in, which is not part of the script.
          this.options.onWord?.(Math.max(
            piece.start, piece.start + event.charIndex - LEAD_IN.length))
        }
      }
    }

    window.speechSynthesis.speak(utterance)
    this.startKeepAlive()
  }

  private finish(mine: number): void {
    if (mine !== this.token) return
    this.stopKeepAlive()
    this.queue = []
    this.setStatus("idle")
    this.options.onEnd?.()
  }

  pause(): void {
    if (!speechSupported()) return
    if (this.gap !== null) {
      // Paused in the silence between two sentences, where there is nothing
      // for the synthesiser to pause. Hold the next sentence instead.
      window.clearTimeout(this.gap)
      this.gap = null
      this.waitingToResume = true
    }
    window.speechSynthesis.pause()
    this.stopKeepAlive()
    this.setStatus("paused")
  }

  resume(): void {
    if (!speechSupported()) return
    if (this.waitingToResume) {
      this.waitingToResume = false
      this.setStatus("speaking")
      this.say(this.token)
      return
    }
    window.speechSynthesis.resume()
    this.setStatus("speaking")
    this.startKeepAlive()
  }

  /** Silence, without reporting an end to whoever asked for the speech. */
  stop(): void {
    if (!speechSupported()) return
    this.token++
    this.stopKeepAlive()
    if (this.gap !== null) {
      window.clearTimeout(this.gap)
      this.gap = null
    }
    this.waitingToResume = false
    this.queue = []
    this.at = 0
    window.speechSynthesis.cancel()
    if (this.status !== "unsupported") this.setStatus("idle")
  }

  private startKeepAlive() {
    this.stopKeepAlive()
    // Chrome stops speaking after about fifteen seconds unless nudged. A
    // resume() on a synthesiser that is already speaking is a no-op
    // everywhere else, so this is safe to run unconditionally.
    this.keepAlive = window.setInterval(() => {
      if (window.speechSynthesis.speaking && !window.speechSynthesis.paused) {
        window.speechSynthesis.resume()
      }
    }, KEEPALIVE_MS)
  }

  private stopKeepAlive() {
    if (this.keepAlive !== null) {
      window.clearInterval(this.keepAlive)
      this.keepAlive = null
    }
  }
}
