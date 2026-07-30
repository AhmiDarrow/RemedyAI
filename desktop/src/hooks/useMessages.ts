import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listMessages,
  streamMessage,
  executeCommand,
  editFromMessageApi,
  type StreamProgress,
  type UsagePayload,
} from '../api/messages'
import {
  completeStreamJob,
  detachStreamJob,
  getStreamJob,
  reattachStreamJob,
  registerStreamJob,
  stopStreamJob,
  touchStreamJob,
} from '../sessions/streamJobs'
import type { ChatMessage } from '../types'
import { toolLabel, type ProcessStep } from '../utils/toolLabels'
import { emptyUsage, type UsageSnapshot } from '../utils/tokenCost'
import { clearChatMediaCache } from '../utils/chatMedia'
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
  mode: 'after' | 'interrupt'
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
  const [runUsage, setRunUsage] = useState<UsageSnapshot | null>(null)
  const [streamCtrl, setStreamCtrl] = useState<AbortController | null>(null)
  const [queue, setQueue] = useState<QueuedSend[]>([])
  const [librarySuggest, setLibrarySuggest] = useState<LibrarySuggest | null>(null)
  /** True when streaming but no SSE activity for a while (provider may be stuck). */
  const [streamStalled, setStreamStalled] = useState(false)
  const [stallSeconds, setStallSeconds] = useState(0)
  const streamingRef = useRef(false)
  const sendLockRef = useRef(false)
  /** Last token / tool / progress activity (ms) for stall detection. */
  const lastStreamActivityRef = useRef(0)
  /** Prompt text of the in-flight turn — used by Stop & retry. */
  const lastSentPromptRef = useRef<{
    text: string
    model?: string
    sid?: string
    attachments?: QueuedSend['attachments']
    planMode?: boolean
  } | null>(null)
  const processStepsRef = useRef<ProcessStep[]>([])
  const queueRef = useRef<QueuedSend[]>([])
  const streamCtrlRef = useRef<AbortController | null>(null)
  /** Latest active session — finishOk must not paint onto a switched session. */
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId
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
    setLoading(true)
    setLoadError(null)
    try {
      const msgs = await listMessages(loadId, MESSAGE_PAGE, 0)
      // Ignore stale responses after a session switch.
      if (sessionIdRef.current !== loadId) return
      setMessages(Array.isArray(msgs) ? msgs : [])
      setHasOlder(Array.isArray(msgs) && msgs.length >= MESSAGE_PAGE)
    } catch (e: unknown) {
      if (sessionIdRef.current !== loadId) return
      const msg = e instanceof Error ? e.message : String(e)
      console.warn('[remedy] listMessages failed', loadId, msg)
      setLoadError(msg || 'Failed to load messages')
      // Clear so we never show another session's transcript under a load failure.
      setMessages([])
      setHasOlder(false)
    } finally {
      if (sessionIdRef.current === loadId) {
        setLoading(false)
      }
    }
  }, [sessionId])

  const loadOlder = useCallback(async () => {
    const loadId = sessionIdRef.current
    if (!loadId || loadingOlder || !hasOlder) return
    setLoadingOlder(true)
    try {
      // Newest-window: offset skips that many newest messages → older page.
      const offset = messages.length
      const older = await listMessages(loadId, MESSAGE_PAGE, offset)
      if (sessionIdRef.current !== loadId) return
      const list = Array.isArray(older) ? older : []
      setHasOlder(list.length >= MESSAGE_PAGE)
      if (list.length) {
        setMessages((prev) => {
          const seen = new Set(prev.map((m) => m.id))
          const fresh = list.filter((m) => !seen.has(m.id))
          return [...fresh, ...prev]
        })
      }
    } catch (e: unknown) {
      console.warn('[remedy] loadOlder failed', e instanceof Error ? e.message : e)
    } finally {
      if (sessionIdRef.current === loadId) setLoadingOlder(false)
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
    // Clear focused UI only — do not abort streamCtrl (job owns the controller).
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
    setStreamCtrl(null)
    streamCtrlRef.current = null
    setQueue([])
    queueRef.current = []
    setHasOlder(false)
    clearChatMediaCache()
    void load({ force: true })
    // Re-bind UI if this session still has a live background job.
    if (sessionId) {
      const job = reattachStreamJob(sessionId) || getStreamJob(sessionId)
      if (job?.status === 'running') {
        streamingRef.current = true
        sendLockRef.current = true
        setStreaming(true)
        streamCtrlRef.current = job.controller
        setStreamCtrl(job.controller)
        lastStreamActivityRef.current = job.lastActivityAt || Date.now()
      }
    }
  }, [load])

  useEffect(() => {
    queueRef.current = queue
  }, [queue])

  const drainQueue = useCallback(async () => {
    if (drainingRef.current || streamingRef.current || sendLockRef.current) return
    const next = queueRef.current[0]
    if (!next) return
    drainingRef.current = true
    setQueue((q) => q.slice(1))
    queueRef.current = queueRef.current.slice(1)
    try {
      const fn = sendTurnRef.current
      if (fn) {
        await fn(
          next.text,
          next.model,
          next.sid,
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
      if (sendLockRef.current || streamingRef.current) return
      sendLockRef.current = true
      streamingRef.current = true

      // Match server-side attachment display: markdown images so ChatImage renders.
      let display = text.trim()
      if (hasAtt) {
        const imgs: string[] = []
        const lines: string[] = []
        for (const a of attachments || []) {
          const name = a.name || a.path.split(/[/\\]/).pop() || 'file'
          const path = (a.path || '').replace(/\\/g, '/')
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
      lastStreamActivityRef.current = Date.now()
      lastSentPromptRef.current = {
        text: text.trim() || '(see attached files)',
        model,
        sid: targetId,
        attachments,
        planMode,
      }

      let doneReceived = false

      const isFocusedTurn = () => sessionIdRef.current === targetId

      const bumpActivity = () => {
        lastStreamActivityRef.current = Date.now()
        touchStreamJob(targetId)
        if (!isFocusedTurn()) return
        setStreamStalled(false)
        setStallSeconds(0)
      }

      const finishOk = async () => {
        if (doneReceived) return
        doneReceived = true
        resetStreamBuffers()
        const stepsSnapshot = [...processStepsRef.current]
        const assistantText = streamAccumRef.current
        const thinkingText = thinkingAccumRef.current || null
        // Optimistic: promote stream into a permanent bubble immediately (no blank gap).
        if (assistantText.trim() && sessionIdRef.current === targetId) {
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
        }
        completeStreamJob(targetId, 'done')
        if (isFocusedTurn()) {
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
          window.setTimeout(() => {
            void drainQueue()
          }, 40)
          return
        }
        try {
          const msgs = await listMessages(targetId)
          if (sessionIdRef.current !== targetId) return
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
        // Drain next queued prompt after a tick so React can settle.
        window.setTimeout(() => {
          void drainQueue()
        }, 40)
      }

      const finishErr = async (errMsg: string) => {
        if (doneReceived) return
        doneReceived = true
        resetStreamBuffers()
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
          void drainQueue()
        }, 40)
      }

      const pushSteps = (next: ProcessStep[]) => {
        processStepsRef.current = next
        setProcessSteps(next)
      }

      const ctrl = streamMessage(
        targetId,
        text.trim() || '(see attached files)',
        (token) => {
          bumpActivity()
          // Background jobs: server persists; do not touch focused UI accumulators.
          if (isFocusedTurn()) appendPartialToken(token)
        },
        () => {
          void finishOk()
        },
        (errMsg) => {
          void finishErr(errMsg)
        },
        model,
        (thought) => {
          bumpActivity()
          if (isFocusedTurn()) appendPartialThinking(thought)
        },
        (name, args, callId) => {
          bumpActivity()
          if (!isFocusedTurn()) return
          setActiveTools((prev) => [...prev, { name, status: 'running' }])
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
          pushSteps([...processStepsRef.current, step])
        },
        (name, preview, ok = true, callId) => {
          bumpActivity()
          if (!isFocusedTurn()) return
          setActiveTools((prev) => {
            let done = false
            return prev.map((t) => {
              if (!done && t.name === name && t.status === 'running') {
                done = true
                return { ...t, status: ok ? ('done' as const) : ('error' as const) }
              }
              return t
            })
          })
          const prev = processStepsRef.current
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
          if (isFocusedTurn()) setTaskProgress(info)
        },
        planMode,
        (usage: UsagePayload) => {
          bumpActivity()
          if (!isFocusedTurn()) return
          setRunUsage({
            prompt_tokens: usage.prompt_tokens ?? 0,
            completion_tokens: usage.completion_tokens ?? 0,
            total_tokens:
              usage.total_tokens
              ?? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0),
            estimated_cost_usd: usage.estimated_cost_usd ?? 0,
            source: usage.source,
            model: usage.model ?? model ?? null,
            provider: usage.provider ?? null,
          })
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
      )

      registerStreamJob(targetId, ctrl, model)
      streamCtrlRef.current = ctrl
      setStreamCtrl(ctrl)
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
      opts?: { mode?: 'after' | 'interrupt'; provider?: string },
    ) => {
      const hasAtt = Boolean(attachments?.length)
      if (!text.trim() && !hasAtt) return
      const targetId = sid || sessionId
      if (!targetId) return
      const provider = opts?.provider

      // Busy: queue for after current turn, or interrupt now.
      if (streamingRef.current || sendLockRef.current) {
        const mode = opts?.mode === 'interrupt' ? 'interrupt' : 'after'
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
          const sidAbort = sessionIdRef.current
          if (sidAbort) {
            // Mark job aborted so busy badges clear (mirror stopStreamJob).
            void stopStreamJob(sidAbort).catch(() => {})
          } else {
            streamCtrlRef.current?.abort()
          }
          resetStreamBuffers()
          setStreaming(false)
          setStreamCtrl(null)
          streamCtrlRef.current = null
          setActiveTools([])
          setTaskProgress(null)
          streamingRef.current = false
          sendLockRef.current = false
          setPartialThinking('')
          setPartialText((pt) => {
            if (pt.trim()) {
              const steps = processStepsRef.current
              const assistantMsg: ChatMessage = {
                id: crypto.randomUUID(),
                role: 'assistant',
                content: pt,
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
            return ''
          })
          setProcessSteps([])
          processStepsRef.current = []
          // Put interrupt item at front
          setQueue((q) => [item, ...q.filter((x) => x.id !== item.id)])
          queueRef.current = [item, ...queueRef.current.filter((x) => x.id !== item.id)]
          window.setTimeout(() => {
            void drainQueue()
          }, 50)
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

  // Stall watchdog: no SSE activity while streaming → surface "provider stuck".
  // DeepSeek-class models can hang mid-think/DSML without closing the stream.
  useEffect(() => {
    if (!streaming) {
      setStreamStalled(false)
      setStallSeconds(0)
      return
    }
    const STALL_WARN_MS = 90_000
    const id = window.setInterval(() => {
      if (!streamingRef.current) return
      const idle = Date.now() - (lastStreamActivityRef.current || Date.now())
      const secs = Math.floor(idle / 1000)
      setStallSeconds(secs)
      setStreamStalled(idle >= STALL_WARN_MS)
    }, 2000)
    return () => window.clearInterval(id)
  }, [streaming])

  const stop = useCallback(() => {
    const sid = sessionIdRef.current
    // Focused session only — background jobs keep running until their own stop.
    if (sid) {
      void stopStreamJob(sid)
    } else {
      streamCtrlRef.current?.abort()
      streamCtrl?.abort()
    }
    resetStreamBuffers()
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
    setPartialText((text) => {
      if (text.trim()) {
        const steps = processStepsRef.current
        const assistantMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: text,
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
      return ''
    })
    setProcessSteps([])
    processStepsRef.current = []
    // Drain any prompts queued "after" the stopped turn.
    window.setTimeout(() => {
      void drainQueue()
    }, 40)
  }, [streamCtrl, resetStreamBuffers, drainQueue])

  /** Stop the stuck turn and re-send the same prompt (provider reconnect). */
  const stopAndRetry = useCallback(() => {
    const pending = lastSentPromptRef.current
    stop()
    if (!pending?.text?.trim() && !pending?.attachments?.length) return
    window.setTimeout(() => {
      void send(
        pending.text,
        pending.model,
        pending.sid,
        pending.attachments,
        pending.planMode,
        { mode: 'after' },
      )
    }, 80)
  }, [stop, send])

  const cancelQueued = useCallback((id: string) => {
    setQueue((q) => q.filter((x) => x.id !== id))
    queueRef.current = queueRef.current.filter((x) => x.id !== id)
  }, [])

  const clearQueue = useCallback(() => {
    setQueue([])
    queueRef.current = []
  }, [])

  const updateQueued = useCallback((id: string, patch: Partial<QueuedSend>) => {
    setQueue((q) => q.map((x) => (x.id === id ? { ...x, ...patch } : x)))
    queueRef.current = queueRef.current.map((x) => (x.id === id ? { ...x, ...patch } : x))
  }, [])

  const promoteQueued = useCallback(
    (id: string) => {
      const item = queueRef.current.find((x) => x.id === id)
      if (!item) return
      // Interrupt with this message
      void send(item.text, item.model, item.sid, item.attachments, item.planMode, {
        mode: 'interrupt',
      })
      setQueue((q) => q.filter((x) => x.id !== id))
    },
    [send],
  )

  const beginEdit = useCallback(
    async (msgId: string, fallbackContent?: string): Promise<string | null> => {
      if (!sessionId || streamingRef.current) return null
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === msgId)
        if (idx < 0) return prev.filter((m) => !m.reverted)
        return prev.slice(0, idx)
      })
      try {
        const r = await editFromMessageApi(sessionId, msgId)
        await load()
        const text =
          typeof r.content === 'string' && r.content.length > 0
            ? r.content
            : (fallbackContent ?? '')
        return text
      } catch (e: unknown) {
        console.warn('Edit failed:', e instanceof Error ? e.message : e)
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
    partialText,
    partialThinking,
    activeTools,
    processSteps,
    taskProgress,
    runUsage,
    queue,
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
