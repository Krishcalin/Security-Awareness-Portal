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
 *     an `end` event — it simply stops. These slides run about a minute each,
 *     so without the keep-alive below, every slide would trail off mid-sentence
 *     and the `end` event would never fire, leaving the player stuck.
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
 */

export type NarratorStatus = "idle" | "speaking" | "paused" | "unsupported"

export interface SpeakOptions {
  onEnd?: () => void
  onWord?: (charIndex: number) => void
  onStatus?: (status: NarratorStatus) => void
}

/** Chrome's ~15s cut-off; nudged well inside it. */
const KEEPALIVE_MS = 10_000

/** How long to wait for voices before deciding there are none. */
const VOICE_TIMEOUT_MS = 2_000

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance === "function"
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

export class Narrator {
  private voice: SpeechSynthesisVoice | null = null
  private keepAlive: number | null = null
  /** Stamped on every utterance so a callback from a cancelled one is ignored. */
  private token = 0
  private status: NarratorStatus = "idle"
  private notify: ((status: NarratorStatus) => void) | undefined

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
    this.notify = options.onStatus
    this.stop()

    const mine = ++this.token
    const utterance = new SpeechSynthesisUtterance(text)
    if (this.voice) {
      utterance.voice = this.voice
      utterance.lang = this.voice.lang
    }
    utterance.rate = 1
    utterance.pitch = 1

    utterance.onend = () => {
      if (mine !== this.token) return   // a cancelled utterance reporting in
      this.stopKeepAlive()
      this.setStatus("idle")
      options.onEnd?.()
    }
    utterance.onerror = (event) => {
      if (mine !== this.token) return
      this.stopKeepAlive()
      // "interrupted" and "canceled" are what a deliberate stop looks like;
      // they are not failures and must not advance the slide.
      const reason = (event as SpeechSynthesisErrorEvent).error
      this.setStatus("idle")
      if (reason !== "interrupted" && reason !== "canceled") options.onEnd?.()
    }
    if (options.onWord) {
      utterance.onboundary = (event) => {
        if (mine !== this.token) return
        if (event.name === "word" || event.name === undefined) {
          options.onWord?.(event.charIndex)
        }
      }
    }

    window.speechSynthesis.speak(utterance)
    this.setStatus("speaking")
    this.startKeepAlive()
  }

  pause(): void {
    if (!speechSupported()) return
    window.speechSynthesis.pause()
    this.stopKeepAlive()
    this.setStatus("paused")
  }

  resume(): void {
    if (!speechSupported()) return
    window.speechSynthesis.resume()
    this.setStatus("speaking")
    this.startKeepAlive()
  }

  /** Silence, without reporting an end to whoever asked for the speech. */
  stop(): void {
    if (!speechSupported()) return
    this.token++
    this.stopKeepAlive()
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
