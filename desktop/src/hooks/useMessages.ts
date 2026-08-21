import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listMessages,
  listSessionTodos,
  streamMessage,
  executeCommand,
  editFromMessageApi,
  type StreamProgress,
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
  completeStreamJob,
  detachStreamJob,
  getJobPaint,
  getStreamJob,
  isJobUiCommitted,
  markJobUiCommitted,
  reattachStreamJob,
  registerStreamJob,
  setJobActiveTools,
  setJobProcessSteps,
  setJobRunUsage,
  setJobTaskProgress,
  setJobBuildTodos,
  shouldRestoreStoppedPartial,
  stopStreamJob,
  touchStreamJob,
  withStoppedMarker,
} from '../sessions/streamJobs'
import { promoteQueuedOptions, retrySendOptions } from '../sessions/retryPrompt'
import { steerSession } from '../api/sessions'
import type { ChatMessage } from '../types'
import { toolLabel, type ProcessStep } from '../utils/toolLabels'
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
  /** after = wait for current turn; interrupt = stop current then send */
  mode: 'after' | 'interrupt' | 'steer'
}

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

  const load = useCallback(async (opts?: { force?: boolean }) => {
    if (!sessionId) {
      setMessages([])
      setLoading(false)
      setLoadError(null)
      setHasOlder(false)
      return
    }
    // Always load when switching sessions (force). Only skip mid-stream refreshes
    // for the same session — previously a stuck stream blocked all session switches.
    if (streamingRef.current && !opts?.force) return
    const loadId = sessionId
    const prevGen = loadGenBySessionRef.current.get(loadId) || 0
    const gen = prevGen + 1
    loadGenBySessionRef.current.set(loadId, gen)
    setLoading(true)
    setLoadError(null)
    try {
      const msgs = await listMessages(loadId, MESSAGE_PAGE, 0)
      // Ignore stale responses after a session switch or a newer load/finishOk.
      if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return
      const list = Array.isArray(msgs) ? msgs : []
      serverCountRef.current = list.length
      setMessages(list)
      setHasOlder(list.length >= MESSAGE_PAGE)
      try {
        const td = await listSessionTodos(loadId)
        if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return
        const parsed = parseTodosPayload(td)
        setBuildTodos(todosHaveOpen(parsed) ? parsed : [])
      } catch {
        /* checklist is optional */
      }
    } catch (e: unknown) {
      if ((loadGenBySessionRef.current.get(loadId) || 0) !== gen || sessionIdRef.current !== loadId) return
      const msg = e instanceof Error ? e.message : String(e)
      console.warn('[remedy] listMessages failed', loadId, msg)
      setLoadError(msg || 'Failed to load messages')
      // Clear so we never show another session's transcript under a load failure.
      setMessages([])
      setHasOlder(false)
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
        setMessages((prev) => {
          if (!shouldRestoreStoppedPartial(prev, paint.partialText)) return prev
          return [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: withStoppedMarker(paint.partialText),
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
        )
      }
    } finally {
      drainingRef.current = false
    }
  }, [])

  const sendTurn = useCallback(
    async (
      text: string,
      model?: string,
      sid?: string,
      attachments?: QueuedSend['attachments'],
      planMode?: boolean,
      provider?: string,
    ) => {
      const targetId = sid || sessionId
      const hasAtt = Boolean(attachments?.length)
      if (!targetId || (!text.trim() && !hasAtt)) return
      // Per-session busy — other tabs may stream concurrently.
      if (getStreamJob(targetId)?.status === 'running') return
      const isFocusedStart = sessionIdRef.current === targetId
      if (isFocusedStart) {
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
      }
      lastStreamActivityRef.current = Date.now()
      lastSentBySessionRef.current.set(targetId, {
        text: text.trim() || '(see attached files)',
        model,
        provider,
        sid: targetId,
        attachments,
        planMode,
      })

      let doneReceived = false

      const bumpActivity = () => {
        lastStreamActivityRef.current = Date.now()
        touchStreamJob(targetId)
        if (!isFocusedTurn()) return
        setStreamStalled(false)
        setStallSeconds(0)
      }

      const finishOk = async (meta?: { aborted?: boolean; steered?: boolean }) => {
        if (doneReceived) return
        doneReceived = true
        if (meta?.steered) {
          // Words went to the still-running turn; that turn's own stream (or
          // reattach) paints the reply. Nothing to commit here.
          markJobUiCommitted(targetId)
        }
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
        let assistantText =
          (paint?.partialText && paint.partialText.length
            ? paint.partialText
            : streamAccumRef.current) || ''
        if (wasAborted && assistantText.trim()) {
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
            })),
            tool_results: stepsSnapshot.map((s) => ({
              name: s.name,
              output: s.resultText || '',
              error: s.error,
            })),
            model: model || null,
            agent: null,
            tokens: null,
            created_at: new Date().toISOString(),
            reverted: false,
          }
          setMessages((prev) => [...prev, optimistic])
          if (wasAborted) markJobUiCommitted(targetId)
        }
        completeStreamJob(targetId, wasAborted ? 'aborted' : 'done')
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
        if (wasAborted) {
          setProcessSteps([])
          processStepsRef.current = []
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
                }))
                last.tool_results = stepsSnapshot.map((s) => ({
                  name: s.name,
                  output: s.resultText || '',
                  error: s.error,
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

      const finishErr = async (errMsg: string) => {
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
        if (sessionIdRef.current === targetId) {
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

      // streamMessage returns its AbortController synchronously (before fetch).
      // Register immediately so background tokens always hit job.paint.
      const ctrl = streamMessage(
        targetId,
        text.trim() || '(see attached files)',
        (token) => {
          bumpActivity()
          // Always accumulate on the job so reattach/finish see full text.
          appendJobToken(targetId, token)
          if (isFocusedTurn()) appendPartialToken(token)
        },
        (doneMeta) => {
          void finishOk({
            aborted: Boolean(doneMeta?.aborted),
            steered: Boolean(doneMeta?.steered),
          })
        },
        (errMsg) => {
          void finishErr(errMsg)
        },
        model,
        (thought) => {
          bumpActivity()
          appendJobThinking(targetId, thought)
          if (isFocusedTurn()) appendPartialThinking(thought)
        },
        (name, args, callId) => {
          bumpActivity()
          const paint = getJobPaint(targetId)
          const tools = [
            ...(paint?.activeTools || []),
            { name, status: 'running' as const },
          ]
          setJobActiveTools(targetId, tools)
          if (isFocusedTurn()) {
            setActiveTools(tools)
          }
          const step: ProcessStep = {
            id: callId || `${name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            name,
            label: toolLabel(name),
            status: 'running',
            startedAt: Date.now(),
            callId: callId || undefined,
            argsText:
              args && Object.keys(args).length
                ? JSON.stringify(args, null, 2)
                : undefined,
          }
          const prevSteps =
            paint?.processSteps?.length
              ? paint.processSteps
              : processStepsRef.current
          pushSteps([...prevSteps, step])
        },
        (name, preview, ok = true, callId) => {
          bumpActivity()
          const paint = getJobPaint(targetId)
          let tools = paint?.activeTools || []
          let toolDone = false
          tools = tools.map((t) => {
            if (!toolDone && t.name === name && t.status === 'running') {
              toolDone = true
              return { ...t, status: ok ? ('done' as const) : ('error' as const) }
            }
            return t
          })
          setJobActiveTools(targetId, tools)
          if (isFocusedTurn()) setActiveTools(tools)

          const prev =
            paint?.processSteps?.length
              ? paint.processSteps
              : processStepsRef.current
          let hit = false
          const next = prev.map((s) => {
            if (hit || s.status !== 'running') return s
            const idMatch =
              callId && (s.callId === callId || s.id === callId)
            const nameMatch = !callId && s.name === name
            if (idMatch || nameMatch) {
              hit = true
              return {
                ...s,
                status: (ok ? 'done' : 'error') as ProcessStep['status'],
                endedAt: Date.now(),
                resultText: preview,
                error: ok ? undefined : preview || 'tool failed',
                callId: s.callId || callId,
              }
            }
            return s
          })
          if (!hit) {
            next.push({
              id: callId || `${name}-done-${Date.now()}`,
              name,
              label: toolLabel(name),
              status: ok ? 'done' : 'error',
              startedAt: Date.now(),
              endedAt: Date.now(),
              callId: callId || undefined,
              resultText: preview,
              error: ok ? undefined : preview || 'tool failed',
            })
          }
          pushSteps(next)
        },
        attachments,
        (info) => {
          bumpActivity()
          setJobTaskProgress(targetId, info)
          if (isFocusedTurn()) setTaskProgress(info)
        },
        planMode,
        (usage: UsagePayload) => {
          bumpActivity()
          const snap = {
            prompt_tokens: usage.prompt_tokens ?? 0,
            completion_tokens: usage.completion_tokens ?? 0,
            total_tokens:
              usage.total_tokens
              ?? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0),
            estimated_cost_usd: usage.estimated_cost_usd ?? 0,
            source: usage.source,
            model: usage.model ?? model ?? null,
            provider: usage.provider ?? null,
          }
          setJobRunUsage(targetId, snap)
          if (isFocusedTurn()) setRunUsage(snap)
        },
        (payload) => {
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
            version:
              typeof payload.version === 'string' ? payload.version : undefined,
            reason: typeof payload.reason === 'string' ? payload.reason : undefined,
          })
        },
        provider,
        (payload) => {
          bumpActivity()
          const parsed = parseTodosPayload(payload)
          const live = todosHaveOpen(parsed) ? parsed : []
          setJobBuildTodos(targetId, live)
          if (isFocusedTurn()) setBuildTodos(live)
        },
      )

      registerStreamJob(targetId, ctrl, model)
      // Only bind focused chrome AbortController to this job.
      if (isFocusedTurn()) {
        streamCtrlRef.current = ctrl
        setStreamCtrl(ctrl)
      }
    },
    [
      sessionId,
      appendPartialToken,
      appendPartialThinking,
      resetStreamBuffers,
      clearStreamAccum,
      drainQueue,
    ],
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
      opts?: { mode?: 'after' | 'interrupt' | 'steer'; provider?: string },
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
        // Steer: hand the words to the running turn. Text only — an
        // attachment needs a turn of its own, so that falls back to
        // interrupt, as does a turn that ended between the check and the
        // call.
        if (opts?.mode === 'steer' && !hasAtt) {
          let steered = false
          try {
            steered = (await steerSession(targetId, text)).steered
          } catch {
            steered = false
          }
          if (steered) {
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
        }
        const mode = opts?.mode === 'after' ? 'after' : 'interrupt'
        const item: QueuedSend = {
          id: crypto.randomUUID(),
          text,
          model,
          provider,
          sid: targetId,
          attachments,
          planMode,
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

      await sendTurn(text, model, sid, attachments, planMode, provider)
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
