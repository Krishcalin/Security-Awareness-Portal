import { vi } from "vitest"

/** A stand-in for the browser's speech synthesis, with the behaviours that
 *  actually bite: voices arriving late, and callbacks firing after cancel. */
export function installFakeSpeech(
  voices: Partial<SpeechSynthesisVoice>[] = [
    { name: "Daniel", lang: "en-GB", localService: true },
  ],
) {
  const spoken: any[] = []
  const listeners: Record<string, (() => void)[]> = {}
  let available = voices

  const synth = {
    speaking: false,
    paused: false,
    resumeCount: 0,
    getVoices: () => available,
    speak(utterance: any) {
      spoken.push(utterance)
      synth.speaking = true
    },
    cancel() { synth.speaking = false },
    pause() { synth.paused = true },
    resume() { synth.paused = false; synth.resumeCount++ },
    addEventListener(name: string, fn: () => void) {
      (listeners[name] ??= []).push(fn)
    },
    removeEventListener(name: string, fn: () => void) {
      listeners[name] = (listeners[name] ?? []).filter((f) => f !== fn)
    },
  }

  class FakeUtterance {
    voice: unknown = null
    lang = ""
    rate = 1
    pitch = 1
    onend: (() => void) | null = null
    onerror: ((event: unknown) => void) | null = null
    onboundary: ((event: unknown) => void) | null = null
    constructor(public text: string) {}
  }

  vi.stubGlobal("speechSynthesis", synth)
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance)

  return {
    synth,
    spoken,
    /** Deliver voices later, the way every real browser does. */
    deliverVoices(later: Partial<SpeechSynthesisVoice>[]) {
      available = later
      ;(listeners["voiceschanged"] ?? []).forEach((fn) => fn())
    },
  }
}
