/** Local voice API: speak-back (Kokoro TTS) + hearing (whisper STT).
 *
 * All audio stays on this machine. When the backend engines are missing,
 * speak() callers fall back to OS voices (speechSynthesis) — see useVoice.
 */

import { apiFetch, authHeaders, ensureApiToken, getApiBase } from './client'

export interface VoiceSideStatus {
  available: boolean
  enabled: boolean
  engine: string | null
  reason: string | null
  fallback?: string
  voice?: string
  voices?: string[]
  model?: string
  models?: string[]
  installed?: boolean
  install?: { status?: string; percent?: number; error?: string } | null
}

export interface VoiceStatus {
  tts: VoiceSideStatus
  stt: VoiceSideStatus
  settings: {
    tts_enabled: boolean
    stt_enabled: boolean
    speak_replies: boolean
    voice_override: string
    speed: number
    stt_model: string
    language: string
  }
}

export async function getVoiceStatus(): Promise<VoiceStatus> {
  return apiFetch<VoiceStatus>('/voice/status')
}

export async function patchVoiceSettings(
  patch: Partial<VoiceStatus['settings']>,
): Promise<VoiceStatus['settings']> {
  return apiFetch<VoiceStatus['settings']>('/voice/settings', {
    method: 'POST',
    body: JSON.stringify(patch),
  })
}

export async function installVoice(component: 'tts' | 'stt'): Promise<{ ok: boolean; error?: string }> {
  return apiFetch<{ ok: boolean; error?: string }>('/voice/install', {
    method: 'POST',
    body: JSON.stringify({ component }),
  })
}

/** Synthesize speech; returns a playable object URL, or null → use fallback. */
export async function speakToUrl(
  text: string,
  opts?: { voice?: string; speed?: number },
): Promise<string | null> {
  await ensureApiToken()
  try {
    const res = await fetch(`${getApiBase()}/voice/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ text, voice: opts?.voice, speed: opts?.speed }),
    })
    if (!res.ok) return null
    const blob = await res.blob()
    if (!blob.size) return null
    return URL.createObjectURL(blob)
  } catch {
    return null
  }
}

/** Transcribe a recorded clip locally; null when STT is unavailable. */
export async function transcribeAudio(
  blob: Blob,
): Promise<{ text: string; language?: string } | null> {
  await ensureApiToken()
  try {
    const res = await fetch(`${getApiBase()}/voice/transcribe`, {
      method: 'POST',
      headers: {
        'Content-Type': blob.type || 'audio/webm',
        ...authHeaders(),
      },
      body: blob,
    })
    if (!res.ok) return null
    const data = (await res.json()) as { text?: string; language?: string }
    return typeof data.text === 'string' ? { text: data.text, language: data.language } : null
  } catch {
    return null
  }
}
