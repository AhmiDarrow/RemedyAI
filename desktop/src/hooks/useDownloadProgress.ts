/** Poll voice / vision / Hugging Face so the title bar can show any download. */

import { useEffect, useState } from 'react'
import { getHfProgress } from '../api/rmb'
import { getVisionStatus } from '../api/vision'
import { getVoiceStatus } from '../api/voice'
import {
  collectDownloadJobs,
  downloadCaption,
  primaryDownload,
  type DownloadJob,
} from '../downloads/jobs'
import {
  mergeDownloadJobs,
  subscribeDownloads,
  takeOptimisticJobs,
} from '../downloads/live'

function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  return p.catch(() => fallback)
}

/** Idle (not running, no install in flight) vision status poll interval. */
export const VISION_IDLE_POLL_MS = 45_000

const VISION_ACTIVE_PHASES = new Set([
  'downloading', 'download', 'extracting', 'extract', 'installing', 'install',
  'verifying', 'unpacking', 'preparing', 'starting', 'loading',
])

/** True when the decoder is running or an install/download is in progress. */
export function visionLooksActive(
  vs: Parameters<typeof collectDownloadJobs>[0]['vision'],
): boolean {
  if (!vs) return false
  if (vs.running) return true
  const phase = (vs.progress?.phase || '').toLowerCase()
  if (VISION_ACTIVE_PHASES.has(phase)) return true
  const done = vs.progress?.bytes_done || 0
  const total = vs.progress?.bytes_total || 0
  return total > 0 && done < total && phase !== 'ready' && phase !== 'idle' && phase !== 'error'
}

export function useDownloadProgress(): {
  jobs: DownloadJob[]
  primary: DownloadJob | null
  caption: string
} {
  const [jobs, setJobs] = useState<DownloadJob[]>(() => takeOptimisticJobs())

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    // Latest snapshot per source. Every publish re-derives from these, so a
    // slow side poll can only refresh its own sources — never put back an
    // older voice snapshot over a newer one.
    let lastVoice: Parameters<typeof collectDownloadJobs>[0]['voice'] = null
    let lastVision: Parameters<typeof collectDownloadJobs>[0]['vision'] = null
    let lastHf: Parameters<typeof collectDownloadJobs>[0]['hf'] = null
    let sideSeq = 0
    // Vision status is cheap but not free (~150 ms of file probes when the
    // local model is stopped). When nothing is downloading and the decoder
    // is not running, ask at most every VISION_IDLE_POLL_MS instead of every
    // tick — same in Tauri and the WebUI (shared hook).
    let lastVisionAt = 0

    function publish(): number {
      const next = collectDownloadJobs({
        voice: lastVoice,
        vision: lastVision,
        hf: lastHf,
      })
      setJobs(mergeDownloadJobs(next, takeOptimisticJobs()))
      return next.length
    }

    async function tick() {
      let busy = takeOptimisticJobs().length > 0
      try {
        const voice = await safe(getVoiceStatus({ timeout: 4000 }), null)
        if (cancelled) return
        lastVoice = voice
        busy = busy || publish() > 0

        // Side polls must never block the voice bar.
        const mySeq = ++sideSeq
        void (async () => {
          const now = Date.now()
          const pollVision =
            visionLooksActive(lastVision)
            || now - lastVisionAt >= VISION_IDLE_POLL_MS
          const [vision, hfWrap] = await Promise.all([
            pollVision
              ? safe(getVisionStatus({ timeout: 4000 }), null)
              : Promise.resolve(lastVision),
            safe(getHfProgress(), null),
          ])
          // A newer side poll already landed — drop this older answer.
          if (cancelled || mySeq !== sideSeq) return
          if (pollVision) lastVisionAt = now
          lastVision = vision
          lastHf = hfWrap?.progress || null
          publish()
        })()
      } catch {
        if (!cancelled) setJobs(takeOptimisticJobs())
      }
      if (!cancelled) {
        timer = setTimeout(() => void tick(), busy ? 700 : 2500)
      }
    }

    void tick()
    const unsub = subscribeDownloads(() => {
      setJobs((cur) => mergeDownloadJobs(cur, takeOptimisticJobs()))
    })
    return () => {
      cancelled = true
      unsub()
      if (timer) clearTimeout(timer)
    }
  }, [])

  return {
    jobs,
    primary: primaryDownload(jobs),
    caption: downloadCaption(jobs),
  }
}
