/** Settings → Voice — speak, hear, and (Advanced) turn-taking. */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  getVoiceStatus,
  installVoice,
  patchVoiceSettings,
  type VoiceStatus,
} from '../../api/voice'
import type { SettingsMode } from '../../utils/settingsMode'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormHint,
  FormLabel,
  FormNotice,
  FormRange,
  FormSelect,
  FormToggle,
} from './formUi'

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

export function VoiceSection({
  sectionProps,
  settingsMode = 'simple',
}: {
  sectionProps: SectionProps
  settingsMode?: SettingsMode
}): ReactNode {
  const advanced = settingsMode === 'advanced'
  const [status, setStatus] = useState<VoiceStatus | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(() => {
    getVoiceStatus()
      .then(setStatus)
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const installing = Boolean(
    status?.tts.install?.status === 'downloading'
    || status?.stt.install?.status === 'downloading'
    || status?.smart_turn?.install?.status === 'downloading',
  )

  useEffect(() => {
    if (!installing) return
    const t = window.setInterval(refresh, 800)
    return () => window.clearInterval(t)
  }, [installing, refresh])

  const speakReplies = status?.settings.speak_replies ?? false
  const tts = status?.tts
  const stt = status?.stt
  const turn = status?.smart_turn
  const ttsDownloading = tts?.install?.status === 'downloading'
  const sttDownloading = stt?.install?.status === 'downloading'
  const turnDownloading = turn?.install?.status === 'downloading'

  const startInstall = async (component: 'tts' | 'stt' | 'smart-turn') => {
    setBusy(component)
    setMsg('')
    try {
      const r = await installVoice(component)
      if (!r.ok) {
        setMsg([r.error, r.hint].filter(Boolean).join(' '))
      }
      await refresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const patch = async (partial: Partial<VoiceStatus['settings']>) => {
    try {
      await patchVoiceSettings(partial)
      await refresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    }
  }

  const ttsReady = Boolean(tts?.available)
  const pct = (side: { install?: { percent?: number } | null } | undefined) => {
    const n = side?.install?.percent
    return typeof n === 'number' && n > 0 ? ` ${Math.round(n)}%` : ''
  }

  return (
    <SettingsSection {...sectionProps}>
      <FormToggle
        checked={speakReplies}
        onChange={(on) => void patch({ speak_replies: on })}
        label="Speak replies aloud"
        description={
          ttsReady
            ? "Grove uses Remedy's voice on this computer. Audio never leaves the machine."
            : "Works now with this computer's voices. Download Remedy's voice below for clearer speech."
        }
      />

      {!ttsReady ? (
        <div className="flex flex-wrap gap-1.5 mb-2">
          <FormActionButton
            variant="primary"
            disabled={Boolean(busy) || ttsDownloading || tts?.deps === false}
            onClick={() => void startInstall('tts')}
          >
            {ttsDownloading
              ? `Downloading Remedy's voice…${pct(tts)}`
              : "Download Remedy's voice (~340 MB)"}
          </FormActionButton>
        </div>
      ) : (
        <FormHint>Remedy's speaking voice is ready on this computer.</FormHint>
      )}

      {tts?.deps === false || stt?.deps === false ? (
        <FormNotice tone="warn">
          {tts?.reason || stt?.reason || 'The voice pack is not in this install.'}
          {advanced && (tts?.hint || stt?.hint) ? (
            <div className="mt-1 font-mono">{tts?.hint || stt?.hint}</div>
          ) : null}
        </FormNotice>
      ) : null}

      {advanced ? (
        <>
          <FormToggle
            checked={Boolean(tts?.enabled ?? true)}
            onChange={(on) => void patch({ tts_enabled: on })}
            label="Speaking (Kokoro)"
          />
          <FormToggle
            checked={Boolean(stt?.enabled ?? true)}
            onChange={(on) => void patch({ stt_enabled: on })}
            label="Hearing (whisper)"
          />
          {!stt?.installed && stt?.deps !== false ? (
            <FormActionButton
              disabled={Boolean(busy) || sttDownloading}
              onClick={() => void startInstall('stt')}
            >
              {sttDownloading
                ? `Warming hearing…${pct(stt)}`
                : 'Download hearing (whisper)'}
            </FormActionButton>
          ) : null}
          {(stt?.models?.length ?? 0) > 0 ? (
            <>
              <FormLabel>Hearing model</FormLabel>
              <FormSelect
                value={status?.settings.stt_model || 'small'}
                onChange={(v) => void patch({ stt_model: v })}
                options={(stt?.models || []).map((m) => ({ value: m, label: m }))}
              />
            </>
          ) : null}
          {(tts?.voices?.length ?? 0) > 0 ? (
            <>
              <FormLabel>Voice</FormLabel>
              <FormSelect
                value={status?.settings.voice_override || ''}
                onChange={(v) => void patch({ voice_override: v })}
                options={[
                  { value: '', label: 'Follow partner gender' },
                  ...(tts?.voices || []).map((v) => ({ value: v, label: v })),
                ]}
              />
            </>
          ) : null}
          <FormLabel>
            Speed ({(status?.settings.speed ?? 1).toFixed(1)}×)
          </FormLabel>
          <FormRange
            min={50}
            max={200}
            step={10}
            value={Math.round((status?.settings.speed ?? 1) * 100)}
            onChange={(n) => void patch({ speed: n / 100 })}
          />

          <FormLabel>Turn-taking (live calls)</FormLabel>
          <FormHint>
            About 9 MB, BSD-2, from Pipecat. So Remedy does not talk over you.
            Falls back to energy detection until downloaded.
          </FormHint>
          {turn?.available ? (
            <FormHint>Turn-taking is ready.</FormHint>
          ) : (
            <FormActionButton
              disabled={Boolean(busy) || turnDownloading || turn?.deps === false}
              onClick={() => void startInstall('smart-turn')}
            >
              {turnDownloading
                ? `Downloading turn-taking…${pct(turn)}`
                : 'Download turn-taking (~9 MB)'}
            </FormActionButton>
          )}
          {turn?.deps === false && turn.reason ? (
            <FormNotice tone="warn">
              {turn.reason}
              {turn.hint ? <div className="mt-1 font-mono">{turn.hint}</div> : null}
            </FormNotice>
          ) : null}
        </>
      ) : (
        <FormHint>
          Hearing, speed, and live-call turn-taking live under Advanced.
        </FormHint>
      )}

      {msg ? (
        <FormNotice tone="error">{msg}</FormNotice>
      ) : null}
    </SettingsSection>
  )
}
