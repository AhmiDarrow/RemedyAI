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

  const stopSpeaking = useCallback(() => {
    speakGenRef.current += 1
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
      const clean = stripMarkdownForSpeech(text)
      const speakNow = (voices: SpeechSynthesisVoice[]) => {
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
          speakNow(synth.getVoices() || [])
        }
        synth.addEventListener('voiceschanged', handler)
        // Fallback if the event never fires
        setTimeout(() => {
          synth.removeEventListener('voiceschanged', handler)
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
      const url = await speakToUrl(t)
      // A newer speak/stop happened while we were synthesizing — drop this one.
      if (gen !== speakGenRef.current) {
        if (url) URL.revokeObjectURL(url)
        return
      }
      if (url) {
        urlRef.current = url
        const audio = new Audio(url)
        audioRef.current = audio
        audio.onended = () => {
          setSpeaking(false)
          if (urlRef.current === url) {
            URL.revokeObjectURL(url)
            urlRef.current = null
          }
        }
        audio.onerror = () => {
          setSpeaking(false)
          if (urlRef.current === url) {
            URL.revokeObjectURL(url)
            urlRef.current = null
          }
        }
        setSpeaking(true)
        try {
          await audio.play()
          return
        } catch {
          setSpeaking(false)
        }
      }
      // Local engine unavailable → OS voices, matched to the gender role.
      speakViaBrowser(t)
    },
    [enabled, stopSpeaking, speakViaBrowser],
  )

  const startRecording = useCallback(async (): Promise<boolean> => {
    if (!micSupported || recording) return false
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
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
  useEffect(
    () => () => {
      stopSpeaking()
      try {
        recRef.current?.stop()
      } catch {
        /* */
      }
      streamRef.current?.getTracks().forEach((t) => t.stop())
    },
    [stopSpeaking],
  )

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
