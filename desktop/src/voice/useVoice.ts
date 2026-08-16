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
      const utter = new SpeechSynthesisUtterance(text)
      const voices = synth.getVoices() || []
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
    },
    [gender],
  )

  const speak = useCallback(
    async (text: string) => {
      const t = (text || '').trim()
      if (!t || !enabled) return
      stopSpeaking()
      const url = await speakToUrl(t)
      if (url) {
        urlRef.current = url
        const audio = new Audio(url)
        audioRef.current = audio
        audio.onended = () => {
          setSpeaking(false)
          if (urlRef.current) {
            URL.revokeObjectURL(urlRef.current)
            urlRef.current = null
          }
        }
        audio.onerror = () => setSpeaking(false)
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
      rec.onstop = () => resolve()
    })
    try {
      rec.stop()
    } catch {
      /* */
    }
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
