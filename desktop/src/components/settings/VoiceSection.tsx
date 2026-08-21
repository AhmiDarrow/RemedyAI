/** Settings → Voice — speak, hear, and (Advanced) turn-taking. */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  getVoiceStatus,
  installVoice,
  patchVoiceSettings,
  type VoiceStatus,
} from '../../api/voice'
import { clearOptimisticDownload, noteOptimisticDownload } from '../../downloads/live'
import type { SettingsMode } from '../../utils/settingsMode'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormDownloadProgress,
  FormHint,
  FormLabel,
  FormNotice,
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

  const refresh = useCallback(async () => {
    try {
      setStatus(await getVoiceStatus({ timeout: 8000 }))
    } catch {
      /* keep last status */
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const installing = Boolean(
    busy
    || status?.tts.install?.status === 'downloading'
    || status?.stt.install?.status === 'downloading'
    || status?.stt.install?.status === 'loading'
    || status?.smart_turn?.install?.status === 'downloading'
    || status?.hq?.install?.status === 'downloading'
    || status?.pack?.install?.status === 'downloading',
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
  const sttDownloading =
    stt?.install?.status === 'downloading' || stt?.install?.status === 'loading'
  const turnDownloading = turn?.install?.status === 'downloading'

  const startInstall = async (
    component: 'tts' | 'stt' | 'smart-turn' | 'chatterbox' | 'all',
  ) => {
    setBusy(component)
    setMsg('')
    const label =
      component === 'chatterbox'
        ? "Remedy's voice"
        : component === 'smart-turn'
          ? 'Turn-taking'
          : component === 'stt'
            ? 'Hearing'
            : "Downloading Remedy's voice"
    noteOptimisticDownload({ id: 'voice', label, percent: null })
    try {
      const r = await installVoice(component)
      const st = await getVoiceStatus({ timeout: 8000 })
      setStatus(st)
      const still = [
        st.pack?.install?.status,
        st.tts.install?.status,
        st.stt.install?.status,
        st.smart_turn?.install?.status,
        st.hq?.install?.status,
      ].some((s) => s === 'downloading' || s === 'loading')
      if (!r.ok) {
        setMsg(r.error || 'Download did not start.')
        if (!still) clearOptimisticDownload('voice')
      } else if (!still) {
        clearOptimisticDownload('voice')
        const err =
          st.pack?.install?.error
          || st.hq?.install?.error
          || st.tts.install?.error
          || st.stt.install?.error
        if (err) setMsg(err)
        else if (r.started === false) {
          /* already in flight or already done — status will say */
        }
      }
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
  const hq = status?.hq
  const pack = status?.pack
  const packMissing = tts?.deps === false || stt?.deps === false
  const packDownloading = pack?.install?.status === 'downloading'
  const packFailed = pack?.install?.status === 'error'
  const hqDownloading = hq?.install?.status === 'downloading'
  const hqReady = Boolean(hq?.available)
  const ttsFailed = tts?.install?.status === 'error'
  const hqFailed = hq?.install?.status === 'error'
  // One voice, two parts: "ready" as soon as she can speak with her own voice
  // at all; the full voice follows in the background.
  const voiceReady = ttsReady || hqReady
  const voiceDownloading =
    packDownloading || ttsDownloading || hqDownloading || busy === 'all' || busy === 'tts'
  const voiceFailed = !voiceReady && (packFailed || ttsFailed || hqFailed)
  const voiceError =
    pack?.install?.error ||
    tts?.install?.error ||
    hq?.install?.error ||
    "Remedy's voice did not finish downloading."
  const voiceProgressLabel =
    (packDownloading || ttsDownloading ? pack?.install?.message : null) ||
    hq?.install?.message ||
    "Downloading Remedy's voice"
  const voiceProgressPercent =
    packDownloading || ttsDownloading
      ? (pack?.install?.percent ?? tts?.install?.percent ?? null)
      : (hq?.install?.percent ?? null)
  const sttFailed = stt?.install?.status === 'error'
  const turnFailed = turn?.install?.status === 'error'

  const assetLine = (
    ready: boolean,
    downloading: boolean,
    failed: boolean,
    label: string,
    downloadingLabel: string,
    retry: () => void,
    retryDisabled: boolean,
    downloadPercent?: number | null,
  ) => {
    if (ready) return <FormHint>{label} is ready on this computer.</FormHint>
    if (downloading) {
      return (
        <FormDownloadProgress
          label={downloadingLabel.replace(/\s+\d+%$/, '')}
          percent={downloadPercent ?? null}
        />
      )
    }
    if (failed) {
      return (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <FormNotice tone="warn">{label} did not finish downloading.</FormNotice>
          <FormActionButton disabled={retryDisabled} onClick={retry}>
            Retry
          </FormActionButton>
        </div>
      )
    }
    return <FormHint>{label} downloads with Remedy — no extra step.</FormHint>
  }

  return (
    <SettingsSection {...sectionProps}>
      {tts?.enabled === false ? (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <FormNotice tone="warn">
            Remedy's own voice is switched off — this computer's built-in voice is reading
            replies instead of Remedy's own voice.
          </FormNotice>
          <FormActionButton variant="primary" onClick={() => void patch({ tts_enabled: true })}>
            Use Remedy's voice
          </FormActionButton>
        </div>
      ) : null}
      <FormToggle
        checked={speakReplies}
        onChange={(on) => void patch({ speak_replies: on })}
        label="Speak replies aloud"
        description={
          ttsReady
            ? "Grove uses Remedy's voice on this computer. Audio never leaves the machine."
            : "Works now with this computer's voices. Remedy's voice arrives with the rest of the install."
        }
      />

      {/* Remedy has one voice. It arrives in two parts (the quick first
          voice, then the full one); the owner sees one line for both. */}
      {voiceReady ? (
        <FormHint>Remedy's voice is ready on this computer.</FormHint>
      ) : voiceDownloading ? (
        <FormDownloadProgress
          label={voiceProgressLabel}
          percent={voiceProgressPercent}
        />
      ) : voiceFailed ? (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <FormNotice tone="warn">{voiceError}</FormNotice>
          <FormActionButton
            variant="primary"
            disabled={Boolean(busy)}
            onClick={() => void startInstall('all')}
          >
            Retry download
          </FormActionButton>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <FormNotice tone="warn">
            {tts?.reason || stt?.reason || "Remedy's voice is not on this computer yet."}
          </FormNotice>
          {pack?.supported === false ? null : (
            <FormActionButton
              variant="primary"
              disabled={Boolean(busy)}
              onClick={() => void startInstall('all')}
            >
              Download Remedy's voice
            </FormActionButton>
          )}
        </div>
      )}
      {voiceReady && !hqReady ? (
        <FormHint>
          {hqDownloading
            ? 'Her full voice is still arriving; she speaks with the first one meanwhile.'
            : 'Her full voice is not here yet; she speaks with the first one meanwhile.'}
        </FormHint>
      ) : null}

      {advanced && packMissing && (tts?.hint || stt?.hint) ? (
        <FormHint className="font-mono">{tts?.hint || stt?.hint}</FormHint>
      ) : null}

      {advanced ? (
        <>
          <FormToggle
            checked={Boolean(stt?.enabled ?? true)}
            onChange={(on) => void patch({ stt_enabled: on })}
            label="Hearing (whisper)"
          />
          {assetLine(
            Boolean(stt?.installed),
            sttDownloading,
            sttFailed,
            'Hearing',
            'Warming hearing…',
            () => void startInstall('stt'),
            Boolean(busy) || sttDownloading || stt?.deps === false,
            stt?.install?.percent,
          )}
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
          <FormLabel>Turn-taking (live calls)</FormLabel>
          <FormHint>
            About 9 MB, BSD-2, from Pipecat. So Remedy does not talk over you.
            Falls back to energy detection until it arrives with the install.
          </FormHint>
          {assetLine(
            Boolean(turn?.available),
            turnDownloading,
            turnFailed,
            'Turn-taking',
            'Downloading turn-taking…',
            () => void startInstall('smart-turn'),
            Boolean(busy) || turnDownloading,
            turn?.install?.percent,
          )}
        </>
      ) : (
        <FormHint>
          Hearing and live-call turn-taking live under Advanced.
        </FormHint>
      )}

      {msg ? (
        <FormNotice tone="error">{msg}</FormNotice>
      ) : null}
    </SettingsSection>
  )
}
