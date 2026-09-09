import { useState, useEffect, useCallback, useRef } from 'react'
import {
  attachTurn,
  listMessages,
  listSessionTodos,
  streamMessage,
  executeCommand,
  editFromMessageApi,
  type StreamProgress,
  type TurnStreamHandlers,
  type UsagePayload,
} from '../api/messages'
import {
  parseTodosPayload,
  todosHaveOpen,
  type BuildTodo,
} from '../components/BuildTodos'
import {
  appendJobThinking,
  appendJobToken,
  replaceJobThinking,
  completeStreamJob,
  detachStreamJob,
  getJobPaint,
  getStreamJob,
  isJobInterrupted,
  isJobUiCommitted,
  markJobInterrupted,
  markJobUiCommitted,
  isFollowingTurn,
  getJobRequestId,
  markLiveTurn,
  reattachStreamJob,
  getJobLastSeq,
  registerStreamJob,
  setJobClaimEpoch,
  setJobLastSeq,
  setJobRequestId,
  setJobActiveTools,
  setJobProcessSteps,
  setJobRunUsage,
  setJobTaskProgress,
  setJobBuildTodos,
  shouldRestoreStoppedPartial,
  stopStreamJob,
  touchStreamJob,
  withInterruptedMarker,
  withStoppedMarker,
} from '../sessions/streamJobs'
import {
  promoteQueuedOptions,
  resolveBusySend,
  retrySendOptions,
} from '../sessions/retryPrompt'
import { reattachPlan } from '../sessions/reattach'
import { getSession, steerSession } from '../api/sessions'
import type { ChatMessage } from '../types'
import {
  applyLiveToolCall,
  applyLiveToolResult,
  type ProcessStep,
} from '../utils/toolLabels'
import { emptyUsage, type UsageSnapshot } from '../utils/tokenCost'
import type { LibrarySuggest } from '../api/skillsLibrary'

/** Newest-window page size (matches server newest-first window). */
const MESSAGE_PAGE = 250

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

export type QueuedSend = {
  id: string
  text: string
  model?: string
  /** Per-session provider (paired with model). */
  provider?: string
  sid?: string
  attachments?: {
    path: string
    name?: string
    mime?: string
    size?: number
    is_image?: boolean
    is_text?: boolean
  }[]
  planMode?: boolean
  chatMode?: boolean
  /** after = wait for current turn; interrupt = stop then send; steer is live, not queued */
  mode: 'after' | 'interrupt' | 'steer'
}

/** How a turn's stream is opened: this webview sends it, or joins one in flight. */
type TurnRun =
  | {
      kind: 'send'
      text: string
      model?: string
      provider?: string
      attachments?: QueuedSend['attachments']
      planMode?: boolean
      chatMode?: boolean
    }
  | { kind: 'attach'; requestId: string; after: number; model?: string }

export function useMessages(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [partialThinking, setPartialThinking] = useState('')
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([])
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>([])
  const [taskProgress, setTaskProgress] = useState<StreamProgress | null>(null)
  const [buildTodos, setBuildTodos] = useState<BuildTodo[]>([])
  const [runUsage, setRunUsage] = useState<UsageSnapshot | null>(null)
  const [streamCtrl, setStreamCtrl] = useState<AbortController | null>(null)
  const [queue, setQueue] = useState<QueuedSend[]>([])
  const [librarySuggest, setLibrarySuggest] = useState<LibrarySuggest | null>(null)
  /** True when streaming but no SSE activity for a while (provider may be stuck). */
  const [streamStalled, setStreamStalled] = useState(false)
  const [stallSeconds, setStallSeconds] = useState(0)
  /** User hid the quiet banner for this turn (does not stop the stream). */
  const [stallBannerDismissed, setStallBannerDismissed] = useState(false)
  /**
   * The server holds a claim on this session but never named the turn, so
   * there is no log to attach to. Rare (older sidecar, claim before first
   * record) — show the working state and let the session-events refresh
   * deliver the reply.
   */
  const [remoteBusy, setRemoteBusy] = useState(false)
  /** Set once `runTurnStream` exists — the load effect runs before it. */
  const attachToTurnRef = useRef<
    ((sid: string, requestId: string, model?: string) => void) | null
  >(null)
  const streamingRef = useRef(false)
  const sendLockRef = useRef(false)
  /** Last token / tool / progress activity (ms) for stall detection. */
  const lastStreamActivityRef = useRef(0)
  /** Prompt text of the in-flight turn — used by Stop & retry. Per session. */
  const lastSentBySessionRef = useRef<
    Map<
      string,
      {
        text: string
        model?: string
        provider?: string
        sid?: string
        attachments?: QueuedSend['attachments']
        planMode?: boolean
        chatMode?: boolean
      }
    >
  >(new Map())
  /** Server-window message count (excludes optimistic rows) for loadOlder offset. */
  const serverCountRef = useRef(0)
  const processStepsRef = useRef<ProcessStep[]>([])
  const queueRef = useRef<QueuedSend[]>([])
  const streamCtrlRef = useRef<AbortController | null>(null)
  /** Latest active session — finishOk must not paint onto a switched session. */
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId
  /** Per-session generation tokens — a finish on tab A must not drop tab B's load. */
  const loadGenBySessionRef = useRef<Map<string, number>>(new Map())
  /** Avoid re-entrant auto-drain while finishing a turn. */
  const drainingRef = useRef(false)
  /** RAF-batched stream text (avoids re-render every token). */
  const partialBufRef = useRef('')
  const partialRafRef = useRef<number | null>(null)
  /** Full assistant text for this turn (optimistic commit before listMessages). */
  const streamAccumRef = useRef('')
  const thinkingBufRef = useRef('')
  const thinkingRafRef = useRef<number | null>(null)
  const thinkingAccumRef = useRef('')
  /** Latest sendTurn for queue drain (avoids stale closures). */
  const sendTurnRef = useRef<
    | ((
        text: string,
        model?: string,
        sid?: string,
        attachments?: QueuedSend['attachments'],
        planMode?: boolean,
        provider?: string,
        chatMode?: boolean,
      ) => Promise<void>)
    | null
  >(null)

  const flushPartialText = useCallback(() => {
    partialRafRef.current = null
    const chunk = partialBufRef.current
    if (!chunk) return
    partialBufRef.current = ''
    setPartialText((prev) => prev + chunk)
  }, [])

  const appendPartialToken = useCallback(
    (token: string) => {
      if (!token) return
      streamAccumRef.current += token
      partialBufRef.current += token
      if (partialRafRef.current == null) {
        partialRafRef.current =
          typeof requestAnimationFrame === 'function'
            ? requestAnimationFrame(flushPartialText)
            : (window.setTimeout(flushPartialText, 32) as unknown as number)
      }
    },
    [flushPartialText],
  )

  const flushPartialThinking = useCallback(() => {
    thinkingRafRef.current = null
    const chunk = thinkingBufRef.current
    if (!chunk) return
    thinkingBufRef.current = ''
    setPartialThinking((prev) => prev + chunk)
  }, [])

  const appendPartialThinking = useCallback(
    (thought: string) => {
      if (!thought) return
      thinkingAccumRef.current += thought
      thinkingBufRef.current += thought
      if (thinkingRafRef.current == null) {
        thinkingRafRef.current =
          typeof requestAnimationFrame === 'function'
            ? requestAnimationFrame(flushPartialThinking)
            : (window.setTimeout(flushPartialThinking, 32) as unknown as number)
      }
    },
    [flushPartialThinking],
  )

  const replacePartialThinking = useCallback(
    (thought: string) => {
      if (thinkingRafRef.current != null) {
        if (typeof cancelAnimationFrame === 'function') {
          cancelAnimationFrame(thinkingRafRef.current)
        } else {
          clearTimeout(thinkingRafRef.current)
        }
        thinkingRafRef.current = null
      }
      thinkingBufRef.current = ''
      thinkingAccumRef.current = thought || ''
      setPartialThinking(thought || '')
    },
    [],
  )

  const resetStreamBuffers = useCallback(() => {
    if (partialRafRef.current != null) {
      if (typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(partialRafRef.current)
      } else {
        clearTimeout(partialRafRef.current)
      }
      partialRafRef.current = null
    }
    if (thinkingRafRef.current != null) {
      if (typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(thinkingRafRef.current)
      } else {
        clearTimeout(thinkingRafRef.current)
      }
      thinkingRafRef.current = null
    }
    if (partialBufRef.current) {
      const left = partialBufRef.current
      partialBufRef.current = ''
      setPartialText((prev) => prev + left)
    }
    if (thinkingBufRef.current) {
      const left = thinkingBufRef.current
      thinkingBufRef.current = ''
      setPartialThinking((prev) => prev + left)
    }
  }, [])

  /** Hard-clear stream buffers at turn start (no flush into UI). */
  const clearStreamAccum = useCallback(() => {
    if (partialRafRef.current != null) {
      if (typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(partialRafRef.current)
      } else {
        clearTimeout(partialRafRef.current)
      }
      partialRafRef.current = null
    }
    if (thinkingRafRef.current != null) {
      if (typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(thinkingRafRef.current)
      } else {
        clearTimeout(thinkingRafRef.current)
      }
      thinkingRafRef.current = null
    }
    partialBufRef.current = ''
    thinkingBufRef.current = ''
    streamAccumRef.current = ''
    thinkingAccumRef.current = ''
  }, [])

  const [loadError, setLoadError] = useState<string | null>(null)
  const [hasOlder, setHasOlder] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)

  /** Load the newest window. Resolves with the list painted, or null when skipped/stale/failed. */
  const load = useCallback(async (opts?: { force?: boolean }): Promise<ChatMessage[] | null> => {
    if (!sessionId) {
      setMessages([])
      setLoading(false)
      setLoadError(null)
      setHasOlder(false)
      return null
    }
    // Always load when switching sessions (force). Only skip mid-stream refreshes
    // for the same session — previously a stuck stream blocked all session switches.
    if (streamingRef.current && !opts?.force) return null
    const loadId = sessionId
    const prevGen = loadGenBySessionRef.current.get(loadId) || 0
    const gen = prevGen + 1
    loadGenBySessionRef.current.set(loadId, gen)
    setLoading(true)
    setLoadError(null)
    try {
      const msgs = await listMessages(loadId, MESSAGE_PAGE, 0)
      // Ignore stale responses after a session switch or a newer load/finishOk.
      if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return null
      const list = Array.isArray(msgs) ? msgs : []
      serverCountRef.current = list.length
      setMessages(list)
      setHasOlder(list.length >= MESSAGE_PAGE)
      try {
        const td = await listSessionTodos(loadId)
        if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return list
        const parsed = parseTodosPayload(td)
        setBuildTodos(todosHaveOpen(parsed) ? parsed : [])
      } catch {
        /* checklist is optional */
      }
      return list
    } catch (e: unknown) {
      if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return null
      const msg = e instanceof Error ? e.message : String(e)
      console.warn('[remedy] listMessages failed', loadId, msg)
      setLoadError(msg || 'Failed to load messages')
      // Clear so we never show another session's transcript under a load failure.
      setMessages([])
      setHasOlder(false)
      return null
    } finally {
      if ((loadGenBySessionRef.current.get(loadId) || 0) === gen && sessionIdRef.current === loadId) {
        setLoading(false)
      }
    }
  }, [sessionId])

  const loadOlder = useCallback(async () => {
    const loadId = sessionIdRef.current
    if (!loadId || loadingOlder || !hasOlder) return
    setLoadingOlder(true)
    try {
      // Newest-window: offset skips that many newest *server* messages.
      const offset = serverCountRef.current
      const older = await listMessages(loadId, MESSAGE_PAGE, offset)
      if (sessionIdRef.current !== loadId) return
      const list = Array.isArray(older) ? older : []
      setHasOlder(list.length >= MESSAGE_PAGE)
      if (list.length) {
        setMessages((prev) => {
          const seen = new Set(prev.map((m) => m.id))
          const fresh = list.filter((m) => !seen.has(m.id))
          serverCountRef.current += fresh.length
          return [...fresh, ...prev]
        })
      }
    } catch (e: unknown) {
      console.warn('[remedy] loadOlder failed', e instanceof Error ? e.message : e)
    } finally {
      setLoadingOlder(false)
    }
  }, [hasOlder, loadingOlder, messages.length])

  // Session change: force-load history. Phase A: detach prior turn (do NOT abort)
  // so background work continues while the user chats in another tab.
  const prevSessionForDetachRef = useRef<string | null>(null)
  const prevStreamingForDetachRef = useRef(false)
  useEffect(() => {
    const prev = prevSessionForDetachRef.current
    const wasStreaming =
      prevStreamingForDetachRef.current || streamingRef.current || sendLockRef.current
    if (prev && prev !== sessionId && wasStreaming) {
      // Keep server turn + SSE alive; only unbind focused UI.
      detachStreamJob(prev)
    }
    prevSessionForDetachRef.current = sessionId || null
    prevStreamingForDetachRef.current = false
    setRemoteBusy(false)
    // Do not paint the previous transcript as the newly focused session.
    setMessages([])
    // Clear focused UI only — do not abort streamCtrl (job owns the controller).
    // Hard-clear stream buffers (no flush) so a finishing background turn cannot
    // inject partials into the newly focused session.
    clearStreamAccum()
    streamingRef.current = false
    sendLockRef.current = false
    setStreaming(false)
    setStreamStalled(false)
    setStallSeconds(0)
    setPartialText('')
    setPartialThinking('')
    setActiveTools([])
    setProcessSteps([])
    processStepsRef.current = []
    setTaskProgress(null)
    setBuildTodos([])
    setStreamCtrl(null)
    streamCtrlRef.current = null
    // Keep the send queue across tab switches so "after" / interrupt items with
    // a sid are not silently dropped when the user leaves mid-turn.
    setHasOlder(false)
    // Do not revoke the blob cache here — the lightbox / still-mounted
    // ChatImages hold those object URLs. LRU eviction in chatMedia is enough.
    void load({ force: true }).then(() => {
      if (!sessionId || sessionIdRef.current !== sessionId) return
      const job = getStreamJob(sessionId)
      const paint = getJobPaint(sessionId)
      if (job?.status === 'aborted' && paint?.partialText?.trim()) {
        const mark = job.interrupted ? withInterruptedMarker : withStoppedMarker
        setMessages((prev) => {
          if (!shouldRestoreStoppedPartial(prev, paint.partialText)) return prev
          return [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: mark(paint.partialText),
              thinking: paint.partialThinking || null,
              tool_calls: [],
              tool_results: [],
              model: null,
              agent: null,
              tokens: null,
              created_at: new Date().toISOString(),
              reverted: false,
            },
          ]
        })
        markJobUiCommitted(sessionId)
      }
      // Reload / SSE drop: the server may still be working this session's
      // turn while no local job exists. `claimed` + `active_request_id` say so,
      // and the turn log replays everything this webview missed.
      void getSession(sessionId)
        .then((sess) => {
          if (sessionIdRef.current !== sessionId) return
          const running = getStreamJob(sessionId)?.status === 'running'
          const plan = reattachPlan({
            liveness: sess,
            localJobRunning: running,
            localRequestId: running ? getJobRequestId(sessionId) : undefined,
          })
          if (plan.kind === 'attach') {
            attachToTurnRef.current?.(sessionId, plan.requestId, sess.model || undefined)
          } else {
            setRemoteBusy(plan.kind === 'working')
          }
        })
        .catch(() => {
          /* liveness is best-effort — an unreachable server paints idle */
        })
    })
    // Re-bind UI if this session still has a live background job.
    // Restore paint buffers so concurrent multi-tab turns do not flash blank.
    if (sessionId) {
      const job = reattachStreamJob(sessionId) || getStreamJob(sessionId)
      if (job?.status === 'running') {
        streamingRef.current = true
        sendLockRef.current = true
        setStreaming(true)
        streamCtrlRef.current = job.controller
        setStreamCtrl(job.controller)
        lastStreamActivityRef.current = job.lastActivityAt || Date.now()
        const paint = getJobPaint(sessionId) || job.paint
        streamAccumRef.current = paint.partialText || ''
        thinkingAccumRef.current = paint.partialThinking || ''
        setPartialText(paint.partialText || '')
        setPartialThinking(paint.partialThinking || '')
        processStepsRef.current = paint.processSteps || []
        setProcessSteps(paint.processSteps || [])
        setActiveTools(paint.activeTools || [])
        setTaskProgress(paint.taskProgress || null)
        setBuildTodos(paint.buildTodos || [])
        if (paint.runUsage) {
          setRunUsage({
            prompt_tokens: paint.runUsage.prompt_tokens ?? 0,
            completion_tokens: paint.runUsage.completion_tokens ?? 0,
            total_tokens: paint.runUsage.total_tokens ?? 0,
            estimated_cost_usd: paint.runUsage.estimated_cost_usd ?? 0,
            source: paint.runUsage.source,
            model: paint.runUsage.model ?? null,
            provider: paint.runUsage.provider ?? null,
          })
        }
      }
    }
    // sessionId is intentional: every tab switch must rebind paint / history.
  }, [load, sessionId, clearStreamAccum])

  useEffect(() => {
    queueRef.current = queue
  }, [queue])

  /**
   * Drain the next queued send for a session.
   * Prefer items for `forSid` (the turn that just finished / focused tab);
   * never jump to another session's queue while this one still has work.
   * Busy state is per-session (job registry) — not global sendLock/streaming.
   */
  const drainQueue = useCallback(async (forSid?: string | null) => {
    if (drainingRef.current) {
      if (forSid) {
        window.setTimeout(() => {
          void drainQueue(forSid)
        }, 40)
      }
      return
    }
    const preferred = forSid || sessionIdRef.current
    // Session-scoped busy: only block drain for a sid that still has a live job.
    if (preferred && getStreamJob(preferred)?.status === 'running') return
    const idx = queueRef.current.findIndex(
      (q) => !preferred || !q.sid || q.sid === preferred,
    )
    if (idx < 0) {
      // Preferred session empty — try any idle session that has queue items.
      const otherIdx = queueRef.current.findIndex((q) => {
        const sid = q.sid || preferred
        return sid && getStreamJob(sid)?.status !== 'running'
      })
      if (otherIdx < 0) return
      const nextOther = queueRef.current[otherIdx]
      drainingRef.current = true
      setQueue((q) => q.filter((_, i) => i !== otherIdx))
      queueRef.current = queueRef.current.filter((_, i) => i !== otherIdx)
      try {
        const fn = sendTurnRef.current
        if (fn) {
          await fn(
            nextOther.text,
            nextOther.model,
            nextOther.sid || undefined,
            nextOther.attachments,
            nextOther.planMode,
            nextOther.provider,
            nextOther.chatMode,
          )
        }
      } finally {
        drainingRef.current = false
      }
      return
    }
    const next = queueRef.current[idx]
    const nextSid = next.sid || preferred
    if (nextSid && getStreamJob(nextSid)?.status === 'running') return
    drainingRef.current = true
    setQueue((q) => q.filter((_, i) => i !== idx))
    queueRef.current = queueRef.current.filter((_, i) => i !== idx)
    try {
      const fn = sendTurnRef.current
      if (fn) {
        await fn(
          next.text,
          next.model,
          next.sid || preferred || undefined,
          next.attachments,
          next.planMode,
          next.provider,
          next.chatMode,
        )
      }
    } finally {
      drainingRef.current = false
    }
  }, [])

  /**
   * Drive one turn's SSE — a turn this webview starts (`send`) or one the
   * server is already running (`attach`, which replays the turn log first).
   * Both paths paint through the same job buffers, so a re-attach after a
   * reload restores the trail exactly as the live stream built it.
   */
  const runTurnStream = useCallback(
    (targetId: string, run: TurnRun) => {
      const model = run.model
      const isFocusedTurn = () => sessionIdRef.current === targetId
      let doneReceived = false

      const bumpActivity = () => {
        lastStreamActivityRef.current = Date.now()
        touchStreamJob(targetId)
        if (!isFocusedTurn()) return
        setStreamStalled(false)
        setStallSeconds(0)
      }

      const finishOk = async (meta?: {
        aborted?: boolean
        steered?: boolean
        interrupted?: boolean
      }) => {
        if (doneReceived) return
        doneReceived = true
        if (meta?.steered) {
          // Words went to the still-running turn; that turn's own stream (or
          // reattach) paints the reply. Nothing to commit here.
          markJobUiCommitted(targetId)
        }
        // Closed without a terminal frame: keep the partial, mark it, re-fetch.
        const wasInterrupted = Boolean(meta?.interrupted) || isJobInterrupted(targetId)
        if (wasInterrupted) markJobInterrupted(targetId)
        // Only flush RAF buffers for the focused turn — otherwise a detached
        // job finish injects ghost partials into the visible session.
        if (isFocusedTurn()) {
          resetStreamBuffers()
        } else {
          clearStreamAccum()
        }
        // Prefer per-job paint (survives detach + concurrent tabs) over hook refs.
        const paint = getJobPaint(targetId)
        const job = getStreamJob(targetId)
        const wasAborted = Boolean(
          meta?.aborted || job?.status === 'aborted',
        )
        const alreadyCommitted = isJobUiCommitted(targetId)
        const stepsSnapshot = paint?.processSteps?.length
          ? [...paint.processSteps]
          : [...processStepsRef.current]
        const rawAssistantText =
          (paint?.partialText && paint.partialText.length
            ? paint.partialText
            : streamAccumRef.current) || ''
        let assistantText = rawAssistantText
        if (wasInterrupted && assistantText.trim()) {
          assistantText = withInterruptedMarker(assistantText)
        } else if (wasAborted && assistantText.trim()) {
          assistantText = withStoppedMarker(assistantText)
        }
        const thinkingText =
          (paint?.partialThinking && paint.partialThinking.length
            ? paint.partialThinking
            : thinkingAccumRef.current) || null
        // Optimistic: promote stream into a permanent bubble immediately (no blank gap).
        // Skip when Stop/interrupt already committed this partial (double-bubble guard).
        if (
          !alreadyCommitted
          && assistantText.trim()
          && sessionIdRef.current === targetId
        ) {
          const optimistic: ChatMessage = {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: assistantText,
            thinking: thinkingText,
            tool_calls: stepsSnapshot.map((s) => ({
              name: s.name,
              args: s.argsText ? safeParseArgs(s.argsText) : {},
              id: s.callId,
            })),
            tool_results: stepsSnapshot.map((s) => ({
              name: s.name,
              output: s.resultText || '',
              error: s.error,
              id: s.callId,
            })),
            model: model || null,
            agent: null,
            tokens: null,
            created_at: new Date().toISOString(),
            reverted: false,
          }
          setMessages((prev) => [...prev, optimistic])
          if (wasAborted || wasInterrupted) markJobUiCommitted(targetId)
        }
        completeStreamJob(targetId, wasAborted || wasInterrupted ? 'aborted' : 'done')
        // Always clear focused chrome locks when the focused job ends; if this
        // was a background job, leave focused locks alone (other tab may stream).
        if (isFocusedTurn()) {
          setBuildTodos((prev) => (todosHaveOpen(prev) ? prev : []))
          setStreaming(false)
          setStreamStalled(false)
          setStallSeconds(0)
          setStreamCtrl(null)
          streamCtrlRef.current = null
          setPartialText('')
          setPartialThinking('')
          setActiveTools([])
          setTaskProgress(null)
          streamingRef.current = false
          sendLockRef.current = false
          streamAccumRef.current = ''
          thinkingAccumRef.current = ''
        }
        // Drop results if the user already switched sessions.
        if (sessionIdRef.current !== targetId) {
          if (wasAborted && !alreadyCommitted && assistantText.trim()) {
            markJobUiCommitted(targetId)
          }
          // Drain that session's queue in the background (does not steal focus).
          window.setTimeout(() => {
            void drainQueue(targetId)
          }, 40)
          return
        }
        // Aborted turns: keep the local Stopped bubble; server often has no final row yet.
        if (wasAborted && !wasInterrupted) {
          setProcessSteps([])
          processStepsRef.current = []
          window.setTimeout(() => {
            void drainQueue(targetId)
          }, 40)
          return
        }
        if (wasInterrupted) {
          // Re-fetch: the server may have committed the row (or still be
          // working). Keep our interrupted partial only when the tail lacks it,
          // then wait for the reply the way a reloaded webview does.
          setProcessSteps([])
          processStepsRef.current = []
          try {
            const prevGen = loadGenBySessionRef.current.get(targetId) || 0
            const gen = prevGen + 1
            loadGenBySessionRef.current.set(targetId, gen)
            const msgs = await listMessages(targetId, MESSAGE_PAGE, 0)
            if ((loadGenBySessionRef.current.get(targetId) || 0) !== gen || sessionIdRef.current !== targetId) return
            const list = Array.isArray(msgs) ? msgs : []
            const keepPartial =
              rawAssistantText.trim() && shouldRestoreStoppedPartial(list, rawAssistantText)
            serverCountRef.current = list.length
            setMessages((prev) => {
              if (!keepPartial) return list
              const local = prev.find(
                (m) => m.role === 'assistant' && m.content === assistantText,
              )
              return local ? [...list, local] : list
            })
            setHasOlder(list.length >= MESSAGE_PAGE)
            // The turn may still be running: the socket dropped, the claim did
            // not. Re-attach and replay instead of leaving the page idle.
            if (getStreamJob(targetId)?.status !== 'running') {
              void getSession(targetId)
                .then((sess) => {
                  if (sessionIdRef.current !== targetId) return
                  const plan = reattachPlan({
                    liveness: sess,
                    localJobRunning: getStreamJob(targetId)?.status === 'running',
                  })
                  if (plan.kind === 'attach') {
                    attachToTurnRef.current?.(targetId, plan.requestId, model)
                  } else {
                    setRemoteBusy(plan.kind === 'working')
                  }
                })
                .catch(() => {
                  /* best-effort */
                })
            }
          } catch {
            /* keep optimistic interrupted bubble */
          }
          window.setTimeout(() => {
            void drainQueue(targetId)
          }, 40)
          return
        }
        try {
          const prevGen = loadGenBySessionRef.current.get(targetId) || 0
          const gen = prevGen + 1
          loadGenBySessionRef.current.set(targetId, gen)
          const msgs = await listMessages(targetId)
          if ((loadGenBySessionRef.current.get(targetId) || 0) !== gen || sessionIdRef.current !== targetId) return
          if (stepsSnapshot.length && msgs.length) {
            const last = msgs[msgs.length - 1]
            if (last && last.role === 'assistant') {
              const hasTools = (last.tool_calls?.length || 0) > 0
              if (!hasTools) {
                last.tool_calls = stepsSnapshot.map((s) => ({
                  name: s.name,
                  args: s.argsText ? safeParseArgs(s.argsText) : {},
                  id: s.callId,
                }))
                last.tool_results = stepsSnapshot.map((s) => ({
                  name: s.name,
                  output: s.resultText || '',
                  error: s.error,
                  id: s.callId,
                }))
              }
            }
          }
          setMessages(msgs)
        } catch {
          /* keep optimistic assistant bubble */
        }
        setProcessSteps([])
        processStepsRef.current = []
        // Drain next queued prompt for this session after a tick.
        window.setTimeout(() => {
          void drainQueue(targetId)
        }, 40)
      }

      /**
       * `quiet` is for a re-attach that never joined: failing to reach a turn
       * log is this client's problem, not something the owner did, so it must
       * not land a red system bubble in her transcript.
       */
      const finishErr = async (errMsg: string, opts?: { quiet?: boolean }) => {
        if (doneReceived) return
        doneReceived = true
        if (isFocusedTurn()) {
          resetStreamBuffers()
        } else {
          clearStreamAccum()
        }
        completeStreamJob(targetId, 'error', errMsg)
        if (isFocusedTurn()) {
          setStreaming(false)
          setStreamStalled(false)
          setStallSeconds(0)
          setStreamCtrl(null)
          streamCtrlRef.current = null
          setPartialText('')
          setPartialThinking('')
          setActiveTools([])
          setProcessSteps([])
          processStepsRef.current = []
          setTaskProgress(null)
          streamingRef.current = false
          sendLockRef.current = false
        }
        // Only paint errors on the session that started this turn.
        if (!opts?.quiet && sessionIdRef.current === targetId) {
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'system',
              content: `Error: ${errMsg}`,
              thinking: null,
              tool_calls: [],
              tool_results: [],
              model: null,
              agent: null,
              tokens: null,
              created_at: new Date().toISOString(),
              reverted: false,
            },
          ])
        }
        window.setTimeout(() => {
          void drainQueue(targetId)
        }, 40)
      }

      const pushSteps = (next: ProcessStep[]) => {
        // Always write job paint (background turns keep process trail).
        setJobProcessSteps(targetId, next)
        if (isFocusedTurn()) {
          processStepsRef.current = next
          setProcessSteps(next)
        }
      }

      const handlers: TurnStreamHandlers = {
        onToken: (token) => {
          bumpActivity()
          // Always accumulate on the job so reattach/finish see full text.
          appendJobToken(targetId, token)
          if (isFocusedTurn()) appendPartialToken(token)
        },
        onDone: (doneMeta) => {
          void finishOk({
            aborted: Boolean(doneMeta?.aborted),
            steered: Boolean(doneMeta?.steered),
            interrupted: Boolean(doneMeta?.interrupted),
          })
        },
        onError: (errMsg) => {
          // An attach that never applied a frame did not join the turn (no log
          // for it, or the server moved on). Keep the working state instead of
          // reporting a failure the owner cannot act on.
          const joinFailed = run.kind === 'attach' && getJobLastSeq(targetId) === 0
          if (joinFailed) {
            console.warn('[remedy] could not attach to turn', run.requestId, errMsg)
            setRemoteBusy(true)
          }
          void finishErr(errMsg, { quiet: joinFailed })
        },
        onStart: (info) => {
          if (info.requestId) setJobRequestId(targetId, info.requestId)
          // Stop must send the claim generation, on a re-attach as much as on
          // a turn this webview started.
          if (typeof info.claimEpoch === 'number') {
            setJobClaimEpoch(targetId, info.claimEpoch)
          }
        },
        onSeq: (seq) => {
          setJobLastSeq(targetId, seq)
        },
        onThinking: (thought, meta) => {
          bumpActivity()
          if (meta?.replace) {
            replaceJobThinking(targetId, thought)
            if (isFocusedTurn()) replacePartialThinking(thought)
            return
          }
          appendJobThinking(targetId, thought)
          if (isFocusedTurn()) appendPartialThinking(thought)
        },
        onToolCall: (name, args, callId) => {
          bumpActivity()
          const paint = getJobPaint(targetId)
          const prevSteps = paint?.processSteps?.length
            ? paint.processSteps
            : processStepsRef.current
          const { steps, tools } = applyLiveToolCall(
            prevSteps,
            paint?.activeTools || [],
            { name, args, callId, requestId: getJobRequestId(targetId) },
          )
          setJobActiveTools(targetId, tools)
          markLiveTurn(targetId, 'running')
          if (isFocusedTurn()) setActiveTools(tools)
          pushSteps(steps)
        },
        onToolResult: (name, preview, ok = true, callId) => {
          bumpActivity()
          const paint = getJobPaint(targetId)
          const prevSteps = paint?.processSteps?.length
            ? paint.processSteps
            : processStepsRef.current
          // Results pair by call id (parallel tools finish out of order);
          // name + order only when the frame carries no id.
          const { steps, tools } = applyLiveToolResult(
            prevSteps,
            paint?.activeTools || [],
            { name, preview, ok, callId, requestId: getJobRequestId(targetId) },
          )
          setJobActiveTools(targetId, tools)
          if (isFocusedTurn()) setActiveTools(tools)
          pushSteps(steps)
          const stillRunning = tools.some((t) => t.status === 'running')
          if (!stillRunning) markLiveTurn(targetId, 'verifying')
        },
        onProgress: (info) => {
          bumpActivity()
          setJobTaskProgress(targetId, info)
          if (isFocusedTurn()) setTaskProgress(info)
        },
        onUsage: (usage: UsagePayload) => {
          bumpActivity()
          const snap = {
            prompt_tokens: usage.prompt_tokens ?? 0,
            completion_tokens: usage.completion_tokens ?? 0,
            total_tokens:
              usage.total_tokens
              ?? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0),
            estimated_cost_usd: usage.estimated_cost_usd ?? 0,
            cache_read_tokens: usage.cache_read_tokens,
            cache_write_tokens: usage.cache_write_tokens,
            source: usage.source,
            model: usage.model ?? model ?? null,
            provider: usage.provider ?? null,
          }
          setJobRunUsage(targetId, snap)
          if (isFocusedTurn()) setRunUsage(snap)
        },
        onLibrarySuggest: (payload) => {
          bumpActivity()
          if (sessionIdRef.current !== targetId) return
          const id = typeof payload.id === 'string' ? payload.id : ''
          const name = typeof payload.name === 'string' ? payload.name : ''
          if (!id || !name) return
          setLibrarySuggest({
            id,
            name,
            description:
              typeof payload.description === 'string' ? payload.description : '',
            score: typeof payload.score === 'number' ? payload.score : undefined,
            version: typeof payload.version === 'string' ? payload.version : undefined,
            reason: typeof payload.reason === 'string' ? payload.reason : undefined,
          })
        },
        onTodos: (payload) => {
          bumpActivity()
          const parsed = parseTodosPayload(payload)
          const live = todosHaveOpen(parsed) ? parsed : []
          setJobBuildTodos(targetId, live)
          if (isFocusedTurn()) setBuildTodos(live)
        },
      }

      // Both entry points return their AbortController synchronously (before
      // the first fetch), so the job is registered before any frame lands.
      const ctrl =
        run.kind === 'send'
          ? streamMessage(targetId, run.text, handlers, {
              model,
              provider: run.provider,
              attachments: run.attachments,
              planMode: run.planMode,
              chatMode: run.chatMode,
            })
          : attachTurn(targetId, run.requestId, handlers, { after: run.after })
      registerStreamJob(
        targetId,
        ctrl,
        model,
        run.kind === 'attach'
          ? { requestId: run.requestId, lastSeq: run.after, attached: true }
          : undefined,
      )
      // Only bind focused chrome AbortController to this job.
      if (isFocusedTurn()) {
        streamCtrlRef.current = ctrl
        setStreamCtrl(ctrl)
      }
      return ctrl
    },
    [
      appendPartialToken,
      appendPartialThinking,
      replacePartialThinking,
      resetStreamBuffers,
      clearStreamAccum,
      drainQueue,
    ],
  )

  const attachToTurn = useCallback(
    (sid: string, requestId: string, model?: string) => {
      // Idempotent: a second attach for a turn this webview already paints
      // would replay its frames into a fresh buffer and duplicate the trail.
      if (isFollowingTurn(sid, requestId)) return
      setRemoteBusy(false)
      if (sessionIdRef.current === sid) {
        streamingRef.current = true
        sendLockRef.current = true
        setStreaming(true)
        setStreamStalled(false)
        setStallSeconds(0)
        // The replay is the whole turn — start from a clean paint so nothing
        // from a previous turn is mistaken for this one's work.
        clearStreamAccum()
        setPartialText('')
        setPartialThinking('')
        setActiveTools([])
        setProcessSteps([])
        processStepsRef.current = []
        setTaskProgress(null)
        lastStreamActivityRef.current = Date.now()
      }
      runTurnStream(sid, { kind: 'attach', requestId, after: 0, model })
    },
    [runTurnStream, clearStreamAccum],
  )

  useEffect(() => {
    attachToTurnRef.current = attachToTurn
  }, [attachToTurn])

  const sendTurn = useCallback(
    async (
      text: string,
      model?: string,
      sid?: string,
      attachments?: QueuedSend['attachments'],
      planMode?: boolean,
      provider?: string,
      chatMode?: boolean,
    ) => {
      const targetId = sid || sessionId
      const hasAtt = Boolean(attachments?.length)
      if (!targetId || (!text.trim() && !hasAtt)) return
      // Per-session busy — other tabs may stream concurrently.
      if (getStreamJob(targetId)?.status === 'running') return
      const isFocusedStart = sessionIdRef.current === targetId
      if (isFocusedStart) {
        // This webview now owns the stream — nothing to re-attach to.
        setRemoteBusy(false)
        sendLockRef.current = true
        streamingRef.current = true
      }

      // Match server-side attachment display: markdown images so ChatImage renders.
      // Prefer home-relative attachments/… srcs (reliable /api/media resolve).
      let display = text.trim()
      if (hasAtt) {
        const imgs: string[] = []
        const lines: string[] = []
        for (const a of attachments || []) {
          const name = a.name || a.path.split(/[/\\]/).pop() || 'file'
          let path = (a.path || '').replace(/\\/g, '/')
          const attRel = path.match(/(?:^|\/)\.remedy\/attachments\/(.+)$/i)
            || path.match(/(?:^|\/)attachments\/([^/]+\/[^/]+)$/i)
          if (attRel) {
            path = `attachments/${attRel[1]!.replace(/^attachments\//i, '')}`
          }
          if (a.is_image && path) {
            imgs.push(
              /[\s()]/.test(path) ? `![${name}](<${path}>)` : `![${name}](${path})`,
            )
          }
          lines.push(`- ${name}${a.mime ? ` (${a.mime})` : ''}`)
        }
        const block = [
          ...(imgs.length ? imgs : []),
          imgs.length ? '' : null,
          '📎 Attachments:',
          ...lines,
        ]
          .filter((x) => x != null)
          .join('\n')
        display = display ? `${display}\n\n${block}` : block
      }

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: display,
        thinking: null,
        tool_calls: [],
        tool_results: [],
        model: model || null,
        agent: null,
        tokens: null,
        created_at: new Date().toISOString(),
        reverted: false,
      }

      // Only paint optimistic UI for the focused session. Queued / background
      // turns for another sid must not inject bubbles or streaming chrome here.
      const isFocusedTurn = () => sessionIdRef.current === targetId
      if (isFocusedTurn()) {
        setMessages((prev) => [...prev, userMsg])
        setStreaming(true)
        clearStreamAccum()
        setPartialText('')
        setPartialThinking('')
        setActiveTools([])
        setProcessSteps([])
        processStepsRef.current = []
        setTaskProgress(null)
        setRunUsage(emptyUsage(model || null, null))
        setStreamStalled(false)
        setStallSeconds(0)
        // Endless session: this message is a new beat — don't keep the
        // previous task's open checklist painted until this turn writes one.
        setBuildTodos([])
      }
      lastStreamActivityRef.current = Date.now()
      lastSentBySessionRef.current.set(targetId, {
        text: text.trim() || '(see attached files)',
        model,
        provider,
        sid: targetId,
        attachments,
        planMode,
        chatMode,
      })

      runTurnStream(targetId, {
        kind: 'send',
        text: text.trim() || '(see attached files)',
        model,
        provider,
        attachments,
        planMode,
        chatMode,
      })
    },
    [sessionId, clearStreamAccum, runTurnStream],
  )

  useEffect(() => {
    sendTurnRef.current = sendTurn
  }, [sendTurn])

  const send = useCallback(
    async (
      text: string,
      model?: string,
      sid?: string,
      attachments?: QueuedSend['attachments'],
      planMode?: boolean,
      opts?: { mode?: 'after' | 'interrupt' | 'steer'; provider?: string; chatMode?: boolean },
    ) => {
      const hasAtt = Boolean(attachments?.length)
      if (!text.trim() && !hasAtt) return
      const targetId = sid || sessionId
      if (!targetId) return
      const provider = opts?.provider

      // Busy only if *this* session has a live job (or focused chrome is streaming it).
      const targetBusy =
        getStreamJob(targetId)?.status === 'running' ||
        (sessionIdRef.current === targetId &&
          (streamingRef.current || sendLockRef.current))
      if (targetBusy) {
        // Default is steer: the owner is talking to the live turn.
        // after / interrupt stay explicit. Attachments need a turn of
        // their own, so they never steer (they queue after unless the
        // owner asked to interrupt).
        const explicit = opts?.mode
        const trySteer = !hasAtt && explicit !== 'after' && explicit !== 'interrupt'
        let steered = false
        if (trySteer) {
          try {
            steered = (await steerSession(targetId, text)).steered
          } catch {
            steered = false
          }
        }
        const decision = resolveBusySend({
          explicit,
          hasAttachments: hasAtt,
          steered,
        })
        if (decision === 'steered') {
          if (sessionIdRef.current === targetId) {
            const userMsg: ChatMessage = {
              id: crypto.randomUUID(),
              role: 'user',
              content: text,
              thinking: null,
              tool_calls: [],
              tool_results: [],
              model: null,
              agent: null,
              tokens: null,
              created_at: new Date().toISOString(),
              reverted: false,
            }
            setMessages((prev) => [...prev, userMsg])
          }
          return
        }
        const mode: 'after' | 'interrupt' =
          decision === 'interrupt' ? 'interrupt' : 'after'
        const item: QueuedSend = {
          id: crypto.randomUUID(),
          text,
          model,
          provider,
          sid: targetId,
          attachments,
          planMode,
          chatMode: opts?.chatMode,
          mode,
        }
        if (mode === 'interrupt') {
          // Stop current stream + server turn, then send this first (ahead of after-queue).
          const sidAbort = targetId || sessionIdRef.current
          resetStreamBuffers()
          // Prefer job paint (background / concurrent) over focused partial state.
          const paint = sidAbort ? getJobPaint(sidAbort) : null
          const steps =
            paint?.processSteps?.length
              ? paint.processSteps
              : processStepsRef.current
          const rawText =
            (paint?.partialText && paint.partialText.length
              ? paint.partialText
              : streamAccumRef.current) || ''
          if (sidAbort) {
            try {
              await stopStreamJob(sidAbort)
            } catch {
              /* */
            }
          } else {
            streamCtrlRef.current?.abort()
          }
          const stillFocused = Boolean(sidAbort) && sessionIdRef.current === sidAbort
          if (rawText.trim() && !isJobUiCommitted(sidAbort || '')) {
            if (sidAbort) markJobUiCommitted(sidAbort)
            if (stillFocused) {
              const assistantMsg: ChatMessage = {
                id: crypto.randomUUID(),
                role: 'assistant',
                content: withStoppedMarker(rawText),
                thinking: null,
                tool_calls: steps.map((s) => ({
                  name: s.name,
                  args: s.argsText ? safeParseArgs(s.argsText) : {},
                })),
                tool_results: steps.map((s) => ({
                  name: s.name,
                  output: s.resultText || '',
                  error: s.error,
                })),
                model: null,
                agent: null,
                tokens: null,
                created_at: new Date().toISOString(),
                reverted: false,
              }
              setMessages((prev) => [...prev, assistantMsg])
            }
          }
          if (stillFocused || !sidAbort) {
            setStreaming(false)
            setStreamCtrl(null)
            streamCtrlRef.current = null
            setActiveTools([])
            setTaskProgress(null)
            streamingRef.current = false
            sendLockRef.current = false
            setPartialThinking('')
            setPartialText('')
            streamAccumRef.current = ''
            thinkingAccumRef.current = ''
            setProcessSteps([])
            processStepsRef.current = []
          }
          // Put interrupt item at front
          setQueue((q) => [item, ...q.filter((x) => x.id !== item.id)])
          queueRef.current = [item, ...queueRef.current.filter((x) => x.id !== item.id)]
          // Server abort awaited above — short tick for client cleanup only.
          window.setTimeout(() => {
            void drainQueue(targetId)
          }, 40)
          return
        }
        setQueue((q) => [...q, item])
        queueRef.current = [...queueRef.current, item]
        return
      }

      await sendTurn(text, model, sid, attachments, planMode, provider, opts?.chatMode)
    },
    [sessionId, sendTurn, resetStreamBuffers, drainQueue],
  )

  // Track streaming for session-switch detach (background jobs stay live).
  useEffect(() => {
    if (streaming) prevStreamingForDetachRef.current = true
  }, [streaming])

  // Stall watchdog: no SSE activity while streaming → optional quiet banner.
  // Long-think models (DeepSeek, o-series, etc.) often go 1–3+ min without tokens;
  // keep the gate high so we don't cry wolf, and allow dismiss without stopping.
  useEffect(() => {
    if (!streaming) {
      setStreamStalled(false)
      setStallSeconds(0)
      setStallBannerDismissed(false)
      return
    }
    // 3 minutes — true hangs are rare; long chain-of-thought is common.
    const STALL_WARN_MS = 180_000
    const id = window.setInterval(() => {
      if (!streamingRef.current) return
      const idle = Date.now() - (lastStreamActivityRef.current || Date.now())
      const secs = Math.floor(idle / 1000)
      setStallSeconds(secs)
      setStreamStalled(idle >= STALL_WARN_MS)
    }, 2000)
    return () => window.clearInterval(id)
  }, [streaming])

  const dismissStallBanner = useCallback(() => {
    setStallBannerDismissed(true)
  }, [])

  const stop = useCallback(() => {
    const sid = sessionIdRef.current
    // Flush RAF buffers so paint / accum see the latest tokens before commit.
    resetStreamBuffers()
    // Prefer job paint (survives detach) over focused partialText alone.
    const paint = sid ? getJobPaint(sid) : null
    const steps =
      paint?.processSteps?.length
        ? paint.processSteps
        : processStepsRef.current
    const rawText =
      (paint?.partialText && paint.partialText.length
        ? paint.partialText
        : streamAccumRef.current) || ''
    // Focused session only — background jobs keep running until their own stop.
    const stoppedSid = sid
    if (stoppedSid) {
      void stopStreamJob(stoppedSid).finally(() => {
        void drainQueue(stoppedSid)
      })
    } else {
      streamCtrlRef.current?.abort()
      streamCtrl?.abort()
      window.setTimeout(() => {
        void drainQueue(stoppedSid)
      }, 40)
    }
    if (rawText.trim() && !(sid && isJobUiCommitted(sid))) {
      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: withStoppedMarker(rawText),
        thinking: null,
        tool_calls: steps.map((s) => ({
          name: s.name,
          args: s.argsText ? safeParseArgs(s.argsText) : {},
        })),
        tool_results: steps.map((s) => ({
          name: s.name,
          output: s.resultText || '',
          error: s.error,
        })),
        model: null,
        agent: null,
        tokens: null,
        created_at: new Date().toISOString(),
        reverted: false,
      }
      setMessages((prev) => [...prev, assistantMsg])
      if (sid) markJobUiCommitted(sid)
    }
    setStreaming(false)
    setStreamStalled(false)
    setStallSeconds(0)
    setStreamCtrl(null)
    streamCtrlRef.current = null
    setActiveTools([])
    setTaskProgress(null)
    streamingRef.current = false
    sendLockRef.current = false
    setPartialThinking('')
    setPartialText('')
    streamAccumRef.current = ''
    thinkingAccumRef.current = ''
    setProcessSteps([])
    processStepsRef.current = []
  }, [streamCtrl, resetStreamBuffers, drainQueue])

  /** Stop the stuck turn and re-send the same prompt (provider reconnect). */
  const stopAndRetry = useCallback(() => {
    const sid = sessionIdRef.current
    const pending = sid ? lastSentBySessionRef.current.get(sid) : undefined
    resetStreamBuffers()
    const paint = sid ? getJobPaint(sid) : null
    const steps =
      paint?.processSteps?.length
        ? paint.processSteps
        : processStepsRef.current
    const rawText =
      (paint?.partialText && paint.partialText.length
        ? paint.partialText
        : streamAccumRef.current) || ''
    void (async () => {
      try {
        if (sid) await stopStreamJob(sid)
      } catch {
        /* */
      }
      // Commit the captured sid only — do not re-read sessionIdRef via stop().
      if (sid && sessionIdRef.current === sid) {
        if (rawText.trim() && !isJobUiCommitted(sid)) {
          const assistantMsg: ChatMessage = {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: withStoppedMarker(rawText),
            thinking: null,
            tool_calls: steps.map((s) => ({
              name: s.name,
              args: s.argsText ? safeParseArgs(s.argsText) : {},
            })),
            tool_results: steps.map((s) => ({
              name: s.name,
              output: s.resultText || '',
              error: s.error,
            })),
            model: null,
            agent: null,
            tokens: null,
            created_at: new Date().toISOString(),
            reverted: false,
          }
          setMessages((prev) => [...prev, assistantMsg])
          markJobUiCommitted(sid)
        }
        setStreaming(false)
        setStreamStalled(false)
        setStallSeconds(0)
        setStreamCtrl(null)
        streamCtrlRef.current = null
        setActiveTools([])
        setTaskProgress(null)
        streamingRef.current = false
        sendLockRef.current = false
        setPartialThinking('')
        setPartialText('')
        streamAccumRef.current = ''
        thinkingAccumRef.current = ''
        setProcessSteps([])
        processStepsRef.current = []
      } else if (sid && rawText.trim() && !isJobUiCommitted(sid)) {
        markJobUiCommitted(sid)
      }
      if (!pending?.text?.trim() && !pending?.attachments?.length) return
      await send(
        pending.text,
        pending.model,
        pending.sid,
        pending.attachments,
        pending.planMode,
        // Preserve session LLM bind — without provider, multi-tab retry hits global.
        retrySendOptions(pending),
      )
    })()
  }, [send, resetStreamBuffers])

  const cancelQueued = useCallback((id: string) => {
    setQueue((q) => q.filter((x) => x.id !== id))
    queueRef.current = queueRef.current.filter((x) => x.id !== id)
  }, [])

  const clearQueue = useCallback(() => {
    // Clear only the focused session's queue — other tabs keep their items.
    const sid = sessionIdRef.current
    if (!sid) {
      setQueue([])
      queueRef.current = []
      return
    }
    setQueue((q) => q.filter((x) => x.sid && x.sid !== sid))
    queueRef.current = queueRef.current.filter((x) => x.sid && x.sid !== sid)
  }, [])

  const updateQueued = useCallback((id: string, patch: Partial<QueuedSend>) => {
    setQueue((q) => q.map((x) => (x.id === id ? { ...x, ...patch } : x)))
    queueRef.current = queueRef.current.map((x) => (x.id === id ? { ...x, ...patch } : x))
  }, [])

  const promoteQueued = useCallback(
    (id: string) => {
      const item = queueRef.current.find((x) => x.id === id)
      if (!item) return
      // Interrupt with this message (keep queued provider for multi-tab LLM binds)
      void send(
        item.text,
        item.model,
        item.sid,
        item.attachments,
        item.planMode,
        promoteQueuedOptions(item),
      )
      setQueue((q) => q.filter((x) => x.id !== id))
      queueRef.current = queueRef.current.filter((x) => x.id !== id)
    },
    [send],
  )

  const beginEdit = useCallback(
    async (msgId: string, fallbackContent?: string): Promise<string | null> => {
      if (!sessionId || streamingRef.current) return null
      // Snapshot for rollback if the API fails after optimistic truncate.
      let preEdit: ChatMessage[] | null = null
      setMessages((prev) => {
        preEdit = prev
        const idx = prev.findIndex((m) => m.id === msgId)
        if (idx < 0) return prev.filter((m) => !m.reverted)
        return prev.slice(0, idx)
      })
      try {
        const r = await editFromMessageApi(sessionId, msgId)
        await load({ force: true })
        const text =
          typeof r.content === 'string' && r.content.length > 0
            ? r.content
            : (fallbackContent ?? '')
        return text
      } catch (e: unknown) {
        console.warn('Edit failed:', e instanceof Error ? e.message : e)
        if (preEdit) {
          setMessages(preEdit)
        } else {
          await load({ force: true })
        }
        return fallbackContent ?? null
      }
    },
    [sessionId, load],
  )

  /** Empty the feed in-place (used by /reset) without waiting on the API. */
  const clearLocalHistory = useCallback(() => {
    clearStreamAccum()
    setMessages([])
    setLoadError(null)
    setHasOlder(false)
    setLoading(false)
    setPartialText('')
    setPartialThinking('')
    setActiveTools([])
    setProcessSteps([])
    processStepsRef.current = []
    setTaskProgress(null)
    setBuildTodos([])
    setRunUsage(null)
    setStreamStalled(false)
    setStallSeconds(0)
  }, [clearStreamAccum])

  const runCommand = useCallback(
    async (
      command: string,
      sid?: string,
    ): Promise<{ text: string; action?: string; session_id?: string; cleared?: number }> => {
      const targetId = sid || sessionId
      if (!targetId) return { text: 'No session' }
      try {
        const r = await executeCommand(targetId, command)
        return r
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e || 'request failed')
        return { text: `Error executing ${command}: ${msg}` }
      }
    },
    [sessionId],
  )

  const addCommandMessage = useCallback((command: string, response: string) => {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: command,
      thinking: null,
      tool_calls: [],
      tool_results: [],
      model: null,
      agent: null,
      tokens: null,
      created_at: new Date().toISOString(),
      reverted: false,
    }
    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: response,
      thinking: null,
      tool_calls: [],
      tool_results: [],
      model: null,
      agent: null,
      tokens: null,
      created_at: new Date().toISOString(),
      reverted: false,
    }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
  }, [])

  const clearLibrarySuggest = useCallback(() => setLibrarySuggest(null), [])

  // Composer only shows queue items for the focused session.
  const visibleQueue = queue.filter(
    (q) => !sessionId || !q.sid || q.sid === sessionId,
  )

  return {
    messages,
    loading,
    loadError,
    hasOlder,
    loadingOlder,
    loadOlder,
    streaming,
    remoteBusy,
    streamStalled,
    stallSeconds,
    stallBannerDismissed,
    dismissStallBanner,
    partialText,
    partialThinking,
    activeTools,
    processSteps,
    taskProgress,
    buildTodos,
    runUsage,
    queue: visibleQueue,
    librarySuggest,
    clearLibrarySuggest,
    send,
    stop,
    stopAndRetry,
    cancelQueued,
    clearQueue,
    updateQueued,
    promoteQueued,
    runCommand,
    clearLocalHistory,
    load,
    addCommandMessage,
    beginEdit,
  }
}

function safeParseArgs(raw: string): Record<string, unknown> {
  try {
    const v = JSON.parse(raw)
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}
  } catch {
    return { raw }
  }
}
