/** Optimistic download jobs so the title bar moves the instant the owner clicks. */

import type { DownloadJob } from './jobs'

type Entry = { job: DownloadJob; until: number }

let optimistic: Entry[] = []
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => {
    try {
      fn()
    } catch {
      /* listener */
    }
  })
}

export function noteOptimisticDownload(job: DownloadJob, holdMs = 60_000): void {
  const until = Date.now() + holdMs
  optimistic = optimistic.filter((e) => e.job.id !== job.id)
  optimistic.push({ job: { ...job, percent: job.percent }, until })
  emit()
}

export function clearOptimisticDownload(id: string): void {
  const next = optimistic.filter((e) => e.job.id !== id)
  if (next.length === optimistic.length) return
  optimistic = next
  emit()
}

export function takeOptimisticJobs(): DownloadJob[] {
  const now = Date.now()
  optimistic = optimistic.filter((e) => e.until > now)
  return optimistic.map((e) => e.job)
}

export function subscribeDownloads(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function mergeDownloadJobs(
  server: DownloadJob[],
  extra: DownloadJob[],
): DownloadJob[] {
  const ids = new Set(server.map((j) => j.id))
  return [...server, ...extra.filter((j) => !ids.has(j.id))]
}
