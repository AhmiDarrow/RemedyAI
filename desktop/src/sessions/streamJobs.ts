/**
 * Per-session stream job registry (Phase A).
 * Focused UI paints from useMessages; background jobs keep running after detach.
 */

import { abortSession } from '../api/sessions'

export type StreamJobStatus = 'running' | 'done' | 'error' | 'aborted'

export type StreamJob = {
  sessionId: string
  status: StreamJobStatus
  controller: AbortController
  model?: string
  startedAt: number
  lastActivityAt: number
  error?: string
  /** Detached from focused UI — still running on server. */
  detached: boolean
}

export type StreamJobEvent =
  | { type: 'update'; job: StreamJob }
  | { type: 'removed'; sessionId: string }

const jobs = new Map<string, StreamJob>()
const listeners = new Set<(e: StreamJobEvent) => void>()

function emit(e: StreamJobEvent) {
  for (const l of listeners) {
    try {
      l(e)
    } catch {
      /* ignore subscriber errors */
    }
  }
}

export function subscribeStreamJobs(fn: (e: StreamJobEvent) => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getStreamJob(sessionId: string): StreamJob | undefined {
  return jobs.get(sessionId)
}

export function listStreamJobs(): StreamJob[] {
  return [...jobs.values()]
}

export function getBusySessionIds(): Set<string> {
  const out = new Set<string>()
  for (const j of jobs.values()) {
    if (j.status === 'running') out.add(j.sessionId)
  }
  return out
}

export function countRunningJobs(): number {
  let n = 0
  for (const j of jobs.values()) {
    if (j.status === 'running') n += 1
  }
  return n
}

export function registerStreamJob(
  sessionId: string,
  controller: AbortController,
  model?: string,
): StreamJob {
  // Replace any prior job for this session (single live turn per session).
  const prev = jobs.get(sessionId)
  if (prev && prev.status === 'running' && prev.controller !== controller) {
    try {
      prev.controller.abort()
    } catch {
      /* */
    }
  }
  const job: StreamJob = {
    sessionId,
    status: 'running',
    controller,
    model,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    detached: false,
  }
  jobs.set(sessionId, job)
  emit({ type: 'update', job: { ...job } })
  return job
}

export function touchStreamJob(sessionId: string) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.lastActivityAt = Date.now()
  emit({ type: 'update', job: { ...j } })
}

/** Stop painting this job as the focused stream; leave server turn running. */
export function detachStreamJob(sessionId: string) {
  const j = jobs.get(sessionId)
  if (!j) return
  j.detached = true
  emit({ type: 'update', job: { ...j } })
}

/** Re-bind a live job when the user focuses its session again. */
export function reattachStreamJob(sessionId: string): StreamJob | undefined {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return undefined
  j.detached = false
  emit({ type: 'update', job: { ...j } })
  return j
}

export function completeStreamJob(
  sessionId: string,
  status: 'done' | 'error' | 'aborted',
  error?: string,
) {
  const j = jobs.get(sessionId)
  if (!j) return
  j.status = status
  if (error) j.error = error
  j.lastActivityAt = Date.now()
  emit({ type: 'update', job: { ...j } })
  // Keep completed jobs briefly so UI can show toast / badge clear.
  const later =
    typeof globalThis.setTimeout === 'function'
      ? globalThis.setTimeout.bind(globalThis)
      : (fn: () => void) => {
          fn()
          return 0 as unknown as ReturnType<typeof setTimeout>
        }
  later(() => {
    const cur = jobs.get(sessionId)
    if (cur && cur.status !== 'running') {
      jobs.delete(sessionId)
      emit({ type: 'removed', sessionId })
    }
  }, 4000)
}

/** Abort client stream + server cooperative cancel. */
export async function stopStreamJob(sessionId: string): Promise<void> {
  const j = jobs.get(sessionId)
  if (j) {
    try {
      j.controller.abort()
    } catch {
      /* */
    }
  }
  try {
    await abortSession(sessionId)
  } catch {
    /* */
  }
  completeStreamJob(sessionId, 'aborted')
}

export async function stopAllStreamJobs(): Promise<void> {
  const ids = [...jobs.keys()]
  await Promise.all(ids.map((id) => stopStreamJob(id)))
}
