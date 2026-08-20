/** Active on-disk downloads — one list the title bar (and Settings) can share. */

import type { HfProgress } from '../api/rmb'
import type { VisionStatus } from '../api/vision'
import type { VoiceStatus } from '../api/voice'

export type DownloadJob = {
  id: string
  label: string
  /** 0–100, or null when the size is not known yet. */
  percent: number | null
}

type InstallSide = {
  status?: string
  percent?: number
  message?: string
} | null | undefined

const VISION_BUSY = new Set([
  'downloading',
  'download',
  'extracting',
  'extract',
  'installing',
  'install',
  'verifying',
  'unpacking',
  'preparing',
])

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)))
}

function installBusy(side: InstallSide): boolean {
  const st = (side?.status || '').toLowerCase()
  return st === 'downloading' || st === 'loading'
}

function bestPercent(sides: InstallSide[]): number | null {
  const nums = sides
    .map((s) => s?.percent)
    .filter((n): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0)
  if (!nums.length) return null
  return clampPct(Math.max(...nums))
}

export function collectDownloadJobs(input: {
  voice?: VoiceStatus | null
  vision?: VisionStatus | null
  hf?: HfProgress | null
}): DownloadJob[] {
  const jobs: DownloadJob[] = []
  const v = input.voice
  if (v) {
    const pack = v.pack?.install
    const tts = v.tts?.install
    const stt = v.stt?.install
    const turn = v.smart_turn?.install
    const hq = v.hq?.install
    const active: InstallSide[] = [pack, tts, stt, turn, hq].filter(installBusy)
    if (active.length) {
      const label =
        (pack && installBusy(pack) && (pack.message || "Remedy's voice"))
        || (hq && installBusy(hq) && (hq.message || 'High-quality voice'))
        || (turn && installBusy(turn) && 'Turn-taking')
        || (stt && installBusy(stt) && 'Hearing')
        || "Remedy's voice"
      jobs.push({
        id: 'voice',
        label: String(label),
        percent: bestPercent(active),
      })
    }
  }

  const vs = input.vision
  if (vs?.progress) {
    const phase = (vs.progress.phase || '').toLowerCase()
    const done = vs.progress.bytes_done || 0
    const total = vs.progress.bytes_total || 0
    const busy =
      VISION_BUSY.has(phase)
      || (total > 0 && done < total && phase !== 'ready' && phase !== 'idle' && phase !== 'error')
    if (busy) {
      const pct = total > 0 ? clampPct((100 * done) / total) : null
      jobs.push({
        id: 'vision',
        label: (vs.progress.message || vs.progress.current_file || 'Local model').trim(),
        percent: pct,
      })
    }
  }

  const hf = input.hf
  if (hf) {
    const phase = (hf.phase || '').toLowerCase()
    if (phase === 'downloading' || phase === 'loading') {
      const pct =
        typeof hf.pct === 'number' && Number.isFinite(hf.pct)
          ? clampPct(hf.pct)
          : hf.bytes_total
            ? clampPct((100 * (hf.bytes_done || 0)) / hf.bytes_total)
            : null
      jobs.push({
        id: 'hf',
        label: (hf.message || hf.filename || 'Language model').trim(),
        percent: pct,
      })
    }
  }

  return jobs
}

export function primaryDownload(jobs: DownloadJob[]): DownloadJob | null {
  return jobs[0] || null
}

export function downloadCaption(jobs: DownloadJob[]): string {
  if (!jobs.length) return ''
  return jobs
    .map((j) => (j.percent != null ? `${j.label} ${j.percent}%` : j.label))
    .join(' · ')
}
