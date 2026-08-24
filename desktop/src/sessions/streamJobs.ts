/**
 * Per-session stream job registry (Phase A).
 * Focused UI paints from useMessages; background jobs keep running after detach.
 *
 * Each job owns its own paint buffer so concurrent multi-tab turns never share
 * a single partialText/processSteps accumulator (reattach restores live paint).
 */

import { abortSession } from '../api/sessions'
import type { StreamProgress, UsagePayload } from '../api/messages'
import type { BuildTodo } from '../components/BuildTodos'
import type { ProcessStep } from '../utils/toolLabels'

export type StreamJobStatus = 'running' | 'done' | 'error' | 'aborted'

export type StreamJobActiveTool = { name: string; status: 'running' | 'done' | 'error' }

/** Live stream paint owned by the job (survives session switch / reattach). */
export type StreamJobPaint = {
  partialText: string
  partialThinking: string
  processSteps: ProcessStep[]
  activeTools: StreamJobActiveTool[]
  taskProgress: StreamProgress | null
  buildTodos: BuildTodo[]
  runUsage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    estimated_cost_usd: number
    source?: UsagePayload['source']
    model?: string | null
    provider?: string | null
  } | null
}

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
  /** Accumulated stream paint (tokens/tools) — always updated, even when detached. */
  paint: StreamJobPaint
  /**
   * Focused UI already promoted partial text into a chat bubble (Stop / interrupt).
   * finishOk must not double-commit the same abort.
   */
  uiCommitted?: boolean
}

export function emptyStreamPaint(): StreamJobPaint {
  return {
    partialText: '',
    partialThinking: '',
    processSteps: [],
    activeTools: [],
    taskProgress: null,
    buildTodos: [],
    runUsage: null,
  }
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
    paint: emptyStreamPaint(),
  }
  jobs.set(sessionId, job)
  emit({ type: 'update', job: { ...job } })
  return job
}

/** Throttle App-wide emits — every-token emit re-rendered the whole tree. */
const _lastTouchEmitAt = new Map<string, number>()
const TOUCH_EMIT_MIN_MS = 500

export function touchStreamJob(sessionId: string) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.lastActivityAt = Date.now()
  const now = Date.now()
  const last = _lastTouchEmitAt.get(sessionId) || 0
  if (now - last < TOUCH_EMIT_MIN_MS) return
  _lastTouchEmitAt.set(sessionId, now)
  emit({ type: 'update', job: { ...j } })
}

/** Append assistant token into the job paint buffer (focus-agnostic). */
export function appendJobToken(sessionId: string, token: string) {
  if (!token) return
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.partialText += token
  j.lastActivityAt = Date.now()
}

/** Append thinking token into the job paint buffer. */
export function appendJobThinking(sessionId: string, thought: string) {
  if (!thought) return
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.partialThinking += thought
  j.lastActivityAt = Date.now()
}

/** Start of a new model round — this scratchpad replaces the last one. */
export function replaceJobThinking(sessionId: string, thought: string) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.partialThinking = thought || ''
  j.lastActivityAt = Date.now()
}

export function setJobProcessSteps(sessionId: string, steps: ProcessStep[]) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.processSteps = steps
  j.lastActivityAt = Date.now()
}

export function setJobActiveTools(sessionId: string, tools: StreamJobActiveTool[]) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.activeTools = tools
  j.lastActivityAt = Date.now()
}

export function setJobTaskProgress(sessionId: string, progress: StreamProgress | null) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.taskProgress = progress
  j.lastActivityAt = Date.now()
}

export function setJobBuildTodos(sessionId: string, todos: BuildTodo[]) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.buildTodos = todos
  j.lastActivityAt = Date.now()
}

export function setJobRunUsage(
  sessionId: string,
  usage: StreamJobPaint['runUsage'],
) {
  const j = jobs.get(sessionId)
  if (!j || j.status !== 'running') return
  j.paint.runUsage = usage
  j.lastActivityAt = Date.now()
}

/** Snapshot paint for reattach / finish (shallow copy of arrays). */
export function getJobPaint(sessionId: string): StreamJobPaint | null {
  const j = jobs.get(sessionId)
  if (!j) return null
  const p = j.paint
  return {
    partialText: p.partialText,
    partialThinking: p.partialThinking,
    processSteps: [...p.processSteps],
    activeTools: [...p.activeTools],
    taskProgress: p.taskProgress,
    buildTodos: [...(p.buildTodos || [])],
    runUsage: p.runUsage ? { ...p.runUsage } : null,
  }
}

/** Mark that the UI already committed this turn's partial (Stop / interrupt). */
export function markJobUiCommitted(sessionId: string): void {
  const j = jobs.get(sessionId)
  if (!j) return
  j.uiCommitted = true
  emit({ type: 'update', job: { ...j } })
}

export function isJobUiCommitted(sessionId: string): boolean {
  return Boolean(jobs.get(sessionId)?.uiCommitted)
}

/** Suffix for aborted partials — visible in chat that generation was stopped. */
export const STOPPED_MARKER = '_[Stopped]_'

export function withStoppedMarker(text: string): string {
  const t = (text || '').trimEnd()
  if (!t) return STOPPED_MARKER
  if (t.includes(STOPPED_MARKER)) return t
  return `${t}\n\n${STOPPED_MARKER}`
}

/** True when an aborted partial is missing from the server transcript tail. */
export function shouldRestoreStoppedPartial(
  serverMessages: { role?: string; content?: string }[],
  paintText: string | undefined | null,
): boolean {
  const raw = (paintText || '').trim()
  if (!raw) return false
  for (let i = serverMessages.length - 1; i >= 0; i--) {
    const m = serverMessages[i]
    if (m?.role !== 'assistant') continue
    const content = String(m.content || '')
    if (content.includes(STOPPED_MARKER) || content.includes('Stopped')) return false
    const stem = raw.slice(0, Math.min(80, raw.length))
    if (stem && content.includes(stem)) return false
    break
  }
  return true
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
  // Do not revive a terminal job (e.g. stopStreamJob → aborted, then onDone → done).
  if (j.status !== 'running') return
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
  _lastTouchEmitAt.delete(sessionId)
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
  try {
    await abortSession(sessionId)
  } catch {
    /* */
  }
  const j = jobs.get(sessionId)
  if (j) {
    try {
      j.controller.abort()
    } catch {
      /* */
    }
  }
  completeStreamJob(sessionId, 'aborted')
}

export async function stopAllStreamJobs(): Promise<void> {
  const ids = [...jobs.keys()]
  await Promise.all(ids.map((id) => stopStreamJob(id)))
}
