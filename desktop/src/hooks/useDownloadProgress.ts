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
          const [vision, hfWrap] = await Promise.all([
            safe(getVisionStatus({ timeout: 4000 }), null),
            safe(getHfProgress(), null),
          ])
          // A newer side poll already landed — drop this older answer.
          if (cancelled || mySeq !== sideSeq) return
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
