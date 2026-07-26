import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listMessages,
  streamMessage,
  executeCommand,
  editFromMessageApi,
  type StreamProgress,
  type UsagePayload,
} from '../api/messages'
import type { ChatMessage } from '../types'
import { toolLabel, type ProcessStep } from '../utils/toolLabels'
import { emptyUsage, type UsageSnapshot } from '../utils/tokenCost'

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

export type QueuedSend = {
  id: string
  text: string
  model?: string
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
  const streamingRef = useRef(false)
  const sendLockRef = useRef(false)
  const processStepsRef = useRef<ProcessStep[]>([])
  const queueRef = useRef<QueuedSend[]>([])
  const streamCtrlRef = useRef<AbortController | null>(null)
  /** Avoid re-entrant auto-drain while finishing a turn. */
  const drainingRef = useRef(false)
  /** RAF-batched stream text (avoids re-render every token). */
  const partialBufRef = useRef('')
  const partialRafRef = useRef<number | null>(null)
  const thinkingBufRef = useRef('')
  const thinkingRafRef = useRef<number | null>(null)
  /** Latest sendTurn for queue drain (avoids stale closures). */
  const sendTurnRef = useRef<
    | ((
        text: string,
        model?: string,
        sid?: string,
        attachments?: QueuedSend['attachments'],
        planMode?: boolean,
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

  const load = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      return
    }
    if (streamingRef.current) return
    setLoading(true)
    try {
      const msgs = await listMessages(sessionId)
      setMessages(msgs)
    } catch {
      setMessages([])
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    load()
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
        await fn(next.text, next.model, next.sid, next.attachments, next.planMode)
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
    ) => {
      const targetId = sid || sessionId
      const hasAtt = Boolean(attachments?.length)
      if (!targetId || (!text.trim() && !hasAtt)) return
      if (sendLockRef.current || streamingRef.current) return
      sendLockRef.current = true
      streamingRef.current = true

      let display = text.trim()
      if (hasAtt) {
        const lines = (attachments || []).map(
          (a) => `- ${a.name || a.path}${a.mime ? ` (${a.mime})` : ''}`,
        )
        display = display
          ? `${display}\n\n📎 Attachments:\n${lines.join('\n')}`
          : `📎 Attachments:\n${lines.join('\n')}`
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
      partialBufRef.current = ''
      thinkingBufRef.current = ''
      setPartialText('')
      setPartialThinking('')
      setActiveTools([])
      setProcessSteps([])
      processStepsRef.current = []
      setTaskProgress(null)
      setRunUsage(emptyUsage(model || null, null))

      let doneReceived = false

      const finishOk = async () => {
        if (doneReceived) return
        doneReceived = true
        resetStreamBuffers()
        const stepsSnapshot = [...processStepsRef.current]
        setStreaming(false)
        setStreamCtrl(null)
        streamCtrlRef.current = null
        setPartialText('')
        setPartialThinking('')
        setActiveTools([])
        setTaskProgress(null)
        streamingRef.current = false
        sendLockRef.current = false
        try {
          const msgs = await listMessages(targetId)
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
          /* keep optimistic */
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
        setStreaming(false)
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
        (token) => appendPartialToken(token),
        () => {
          void finishOk()
        },
        (errMsg) => {
          void finishErr(errMsg)
        },
        model,
        (thought) => appendPartialThinking(thought),
        (name, args) => {
          setActiveTools((prev) => {
            if (prev.some((t) => t.name === name && t.status === 'running')) return prev
            return [...prev, { name, status: 'running' }]
          })
          const step: ProcessStep = {
            id: `${name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            name,
            label: toolLabel(name),
            status: 'running',
            startedAt: Date.now(),
            argsText:
              args && Object.keys(args).length
                ? JSON.stringify(args, null, 2)
                : undefined,
          }
          pushSteps([...processStepsRef.current, step])
        },
        (name, preview, ok = true) => {
          setActiveTools((prev) =>
            prev.map((t) =>
              t.name === name
                ? { ...t, status: ok ? ('done' as const) : ('error' as const) }
                : t,
            ),
          )
          const prev = processStepsRef.current
          let hit = false
          const next = prev.map((s) => {
            if (!hit && s.name === name && s.status === 'running') {
              hit = true
              return {
                ...s,
                status: (ok ? 'done' : 'error') as ProcessStep['status'],
                endedAt: Date.now(),
                resultText: preview,
                error: ok ? undefined : preview || 'tool failed',
              }
            }
            return s
          })
          if (!hit) {
            next.push({
              id: `${name}-done-${Date.now()}`,
              name,
              label: toolLabel(name),
              status: ok ? 'done' : 'error',
              startedAt: Date.now(),
              endedAt: Date.now(),
              resultText: preview,
              error: ok ? undefined : preview || 'tool failed',
            })
          }
          pushSteps(next)
        },
        attachments,
        (info) => {
          setTaskProgress(info)
        },
        planMode,
        (usage: UsagePayload) => {
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
      )

      streamCtrlRef.current = ctrl
      setStreamCtrl(ctrl)
    },
    [
      sessionId,
      appendPartialToken,
      appendPartialThinking,
      resetStreamBuffers,
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
      opts?: { mode?: 'after' | 'interrupt' },
    ) => {
      const hasAtt = Boolean(attachments?.length)
      if (!text.trim() && !hasAtt) return
      const targetId = sid || sessionId
      if (!targetId) return

      // Busy: queue for after current turn, or interrupt now.
      if (streamingRef.current || sendLockRef.current) {
        const mode = opts?.mode === 'interrupt' ? 'interrupt' : 'after'
        const item: QueuedSend = {
          id: crypto.randomUUID(),
          text,
          model,
          sid: targetId,
          attachments,
          planMode,
          mode,
        }
        if (mode === 'interrupt') {
          // Stop current stream, then send this first (ahead of after-queue).
          streamCtrlRef.current?.abort()
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
        return
      }

      await sendTurn(text, model, sid, attachments, planMode)
    },
    [sessionId, sendTurn, resetStreamBuffers, drainQueue],
  )

  const stop = useCallback(() => {
    streamCtrlRef.current?.abort()
    streamCtrl?.abort()
    resetStreamBuffers()
    setStreaming(false)
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
  }, [streamCtrl, resetStreamBuffers])

  const cancelQueued = useCallback((id: string) => {
    setQueue((q) => q.filter((x) => x.id !== id))
  }, [])

  const clearQueue = useCallback(() => {
    setQueue([])
    queueRef.current = []
  }, [])

  const updateQueued = useCallback((id: string, patch: Partial<QueuedSend>) => {
    setQueue((q) => q.map((x) => (x.id === id ? { ...x, ...patch } : x)))
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

  const runCommand = useCallback(
    async (
      command: string,
      sid?: string,
    ): Promise<{ text: string; action?: string; session_id?: string }> => {
      const targetId = sid || sessionId
      if (!targetId) return { text: 'No session' }
      try {
        const r = await executeCommand(targetId, command)
        return r
      } catch {
        return { text: `Error executing ${command}` }
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

  return {
    messages,
    loading,
    streaming,
    partialText,
    partialThinking,
    activeTools,
    processSteps,
    taskProgress,
    runUsage,
    queue,
    send,
    stop,
    cancelQueued,
    clearQueue,
    updateQueued,
    promoteQueued,
    runCommand,
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
