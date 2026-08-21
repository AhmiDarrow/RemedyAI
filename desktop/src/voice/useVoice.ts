/** useVoice — speak-back + hearing for the partner surfaces.
 *
 * speak(text): local Kokoro via /voice/speak first (voice follows the
 * assigned gender role server-side); OS speechSynthesis as the zero-install
 * fallback, with the fallback voice matched to the same gender.
 *
 * Mic: MediaRecorder → /voice/transcribe (local whisper). No cloud, ever.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getVoiceStatus, speakToUrl, transcribeAudio, type VoiceStatus } from '../api/voice'
import { pickFallbackVoice, type GenderRole } from './pickVoice'

/** Light markdown → speakable text for the browser-TTS fallback path.
 * (The local Kokoro path is already cleaned server-side by speakable_text.) */
function stripMarkdownForSpeech(md: string): string {
  return (md || '')
    .replace(/```[\s\S]*?```/g, ' code shown on screen. ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 1400)
}

export interface UseVoiceOptions {
  /** Assigned partner gender (agent_gender setting) for fallback voices. */
  gender?: GenderRole
  enabled?: boolean
}

export interface UseVoice {
  status: VoiceStatus | null
  speaking: boolean
  recording: boolean
  transcribing: boolean
  micSupported: boolean
  speak: (text: string) => Promise<void>
  stopSpeaking: () => void
  startRecording: () => Promise<boolean>
  /** Stop + transcribe; resolves to text ('' when nothing recognized/available). */
  stopRecording: () => Promise<string>
  refreshStatus: () => void
}

/** Split a reply into speakable chunks of one or two sentences (≤ ~220 chars). */
/** Window event fired after voice settings change so every useVoice() re-reads. */
export const VOICE_SETTINGS_CHANGED = 'remedy:voice-settings-changed'

export function announceVoiceSettingsChanged(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(VOICE_SETTINGS_CHANGED))
}

export function splitForSpeech(text: string, max = 220): string[] {
  const sentences = text
    .replace(/\s+/g, ' ')
    .split(/(?<=[.!?…])\s+(?=[A-Z0-9"'(\[])/)
    .map((s) => s.trim())
    .filter(Boolean)
  const out: string[] = []
  let cur = ''
  for (const s of sentences) {
    if (!cur) cur = s
    // The first chunk is one sentence so sound starts as soon as possible;
    // later chunks pair short sentences to avoid choppy gaps.
    else if ((out.length > 0 ? cur.length < 90 : cur.length < 40) && cur.length + 1 + s.length <= max)
      cur = `${cur} ${s}`
    else {
      out.push(cur)
      cur = s
    }
  }
  if (cur) out.push(cur)
  return out.length ? out : [text]
}

export function useVoice(opts: UseVoiceOptions = {}): UseVoice {
  const gender: GenderRole = opts.gender || 'female'
  const enabled = opts.enabled !== false
  const [status, setStatus] = useState<VoiceStatus | null>(null)
  const [speaking, setSpeaking] = useState(false)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  // Bumped on every stopSpeaking/speak so a slow in-flight synth can't play
  // over a newer one or leak its object URL.
  const speakGenRef = useRef(0)
  const startingRef = useRef(false)
  const aliveRef = useRef(true)
  const ttsWaitRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const micSupported =
    typeof navigator !== 'undefined'
    && !!navigator.mediaDevices
    && typeof MediaRecorder !== 'undefined'

  const refreshStatus = useCallback(() => {
    if (!enabled) return
    getVoiceStatus()
      .then(setStatus)
      .catch(() => {})
  }, [enabled])

  useEffect(() => {
    refreshStatus()
  }, [refreshStatus])

  // Several surfaces hold their own copy of the voice status (Grove, the
  // status bar). Any of them announces a settings change; all refresh.
  useEffect(() => {
    const onChanged = () => refreshStatus()
    window.addEventListener(VOICE_SETTINGS_CHANGED, onChanged)
    return () => window.removeEventListener(VOICE_SETTINGS_CHANGED, onChanged)
  }, [refreshStatus])

  const stopSpeaking = useCallback(() => {
    speakGenRef.current += 1
    if (ttsWaitRef.current != null) {
      clearTimeout(ttsWaitRef.current)
      ttsWaitRef.current = null
    }
    try {
      audioRef.current?.pause()
    } catch {
      /* */
    }
    audioRef.current = null
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    try {
      window.speechSynthesis?.cancel()
    } catch {
      /* */
    }
    setSpeaking(false)
  }, [])

  const speakViaBrowser = useCallback(
    (text: string) => {
      const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
      if (!synth) return
      const gen = speakGenRef.current
      const clean = stripMarkdownForSpeech(text)
      const speakNow = (voices: SpeechSynthesisVoice[]) => {
        if (gen !== speakGenRef.current) return
        const utter = new SpeechSynthesisUtterance(clean)
        const match = pickFallbackVoice(
          voices.map((v) => ({ name: v.name, lang: v.lang, default: v.default })),
          gender,
        )
        if (match) {
          const real = voices.find((v) => v.name === match.name)
          if (real) utter.voice = real
        }
        utter.onend = () => setSpeaking(false)
        utter.onerror = () => setSpeaking(false)
        setSpeaking(true)
        synth.speak(utter)
      }
      const voices = synth.getVoices() || []
      if (voices.length > 0) {
        speakNow(voices)
      } else {
        // Chromium loads voices async — wait once for the list before picking,
        // else the gender match is lost to the OS default.
        const handler = () => {
          synth.removeEventListener('voiceschanged', handler)
          if (gen !== speakGenRef.current) return
          speakNow(synth.getVoices() || [])
        }
        synth.addEventListener('voiceschanged', handler)
        ttsWaitRef.current = setTimeout(() => {
          ttsWaitRef.current = null
          synth.removeEventListener('voiceschanged', handler)
          if (gen !== speakGenRef.current) return
          if (!synth.speaking) speakNow(synth.getVoices() || [])
        }, 500)
      }
    },
    [gender],
  )

  const speak = useCallback(
    async (text: string) => {
      const t = (text || '').trim()
      if (!t || !enabled) return
      stopSpeaking()
      const gen = speakGenRef.current
      // Pipeline by sentence: the first short chunk plays as soon as it is
      // ready while the next one synthesizes. The high-quality engine runs
      // faster than real time, so playback never waits once it has begun.
      const chunks = splitForSpeech(t)
      const first = await speakToUrl(chunks[0])
      // A newer speak/stop happened while we were synthesizing — drop this one.
      if (gen !== speakGenRef.current) {
        if (first) URL.revokeObjectURL(first)
        return
      }
      if (!first) {
        // Local engine unavailable → OS voices, matched to the gender role.
        speakViaBrowser(t)
        return
      }
      setSpeaking(true)
      let next: Promise<string | null> | null = chunks.length > 1 ? speakToUrl(chunks[1]) : null
      const playUrl = (url: string) =>
        new Promise<boolean>((resolve) => {
          urlRef.current = url
          const audio = new Audio(url)
          audioRef.current = audio
          const done = (ok: boolean) => {
            if (urlRef.current === url) {
              URL.revokeObjectURL(url)
              urlRef.current = null
            }
            resolve(ok)
          }
          audio.onended = () => done(true)
          audio.onerror = () => done(false)
          audio.play().catch(() => done(false))
        })
      let url: string | null = first
      for (let i = 0; url; i += 1) {
        const ok = await playUrl(url)
        if (!ok || gen !== speakGenRef.current) break
        const upcoming: string | null = next ? await next : null
        if (gen !== speakGenRef.current) {
          if (upcoming) URL.revokeObjectURL(upcoming)
          break
        }
        url = upcoming
        next = chunks.length > i + 2 ? speakToUrl(chunks[i + 2]) : null
      }
      if (gen === speakGenRef.current) setSpeaking(false)
    },
    [enabled, stopSpeaking, speakViaBrowser],
  )

  const startRecording = useCallback(async (): Promise<boolean> => {
    if (!micSupported || recording || startingRef.current) return false
    startingRef.current = true
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!aliveRef.current) {
        stream.getTracks().forEach((t) => t.stop())
        return false
      }
      streamRef.current = stream
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : undefined
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      chunksRef.current = []
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      recRef.current = rec
      rec.start(250)
      setRecording(true)
      return true
    } catch {
      return false
    } finally {
      startingRef.current = false
    }
  }, [micSupported, recording])

  const stopRecording = useCallback(async (): Promise<string> => {
    const rec = recRef.current
    if (!rec) return ''
    const done = new Promise<void>((resolve) => {
      // Resolve on onstop, but never hang: if the recorder is already
      // inactive (mic unplugged / permission revoked → rec.stop() throws) or
      // onstop never fires, resolve anyway so handleMic can't wedge.
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        resolve()
      }
      rec.onstop = finish
      if (rec.state === 'inactive') {
        finish()
        return
      }
      try {
        rec.stop()
      } catch {
        finish()
      }
      setTimeout(finish, 4000)
    })
    await done
    setRecording(false)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    recRef.current = null
    const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
    chunksRef.current = []
    if (blob.size < 200) return ''
    setTranscribing(true)
    try {
      const out = await transcribeAudio(blob)
      return out?.text?.trim() || ''
    } finally {
      setTranscribing(false)
    }
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      stopSpeaking()
      try {
        recRef.current?.stop()
      } catch {
        /* */
      }
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [stopSpeaking])

  return {
    status,
    speaking,
    recording,
    transcribing,
    micSupported,
    speak,
    stopSpeaking,
    startRecording,
    stopRecording,
    refreshStatus,
  }
}
