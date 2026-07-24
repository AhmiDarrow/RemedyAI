import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listMessages,
  streamMessage,
  executeCommand,
  editFromMessageApi,
  type StreamProgress,
} from '../api/messages'
import type { ChatMessage } from '../types'
import { toolLabel, type ProcessStep } from '../utils/toolLabels'

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

export function useMessages(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [partialThinking, setPartialThinking] = useState('')
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([])
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>([])
  const [taskProgress, setTaskProgress] = useState<StreamProgress | null>(null)
  const [streamCtrl, setStreamCtrl] = useState<AbortController | null>(null)
  const streamingRef = useRef(false)
  const sendLockRef = useRef(false)
  const processStepsRef = useRef<ProcessStep[]>([])

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

  const send = useCallback(
    async (
      text: string,
      model?: string,
      sid?: string,
      attachments?: {
        path: string
        name?: string
        mime?: string
        size?: number
        is_image?: boolean
        is_text?: boolean
      }[],
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
      setPartialText('')
      setPartialThinking('')
      setActiveTools([])
      setProcessSteps([])
      processStepsRef.current = []
      setTaskProgress(null)

      let doneReceived = false

      const finishOk = async () => {
        if (doneReceived) return
        doneReceived = true
        const stepsSnapshot = [...processStepsRef.current]
        setStreaming(false)
        setStreamCtrl(null)
        setPartialText('')
        setPartialThinking('')
        setActiveTools([])
        setTaskProgress(null)
        streamingRef.current = false
        sendLockRef.current = false
        try {
          const msgs = await listMessages(targetId)
          // Ensure last assistant has process data if server omitted it.
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
          // keep optimistic state
        }
        setProcessSteps([])
        processStepsRef.current = []
      }

      const finishErr = async (errMsg: string) => {
        if (doneReceived) return
        doneReceived = true
        setStreaming(false)
        setStreamCtrl(null)
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
      }

      const pushSteps = (next: ProcessStep[]) => {
        processStepsRef.current = next
        setProcessSteps(next)
      }

      const ctrl = streamMessage(
        targetId,
        text.trim() || '(see attached files)',
        (token) => setPartialText((prev) => prev + token),
        () => {
          void finishOk()
        },
        (errMsg) => {
          void finishErr(errMsg)
        },
        model,
        (thought) => setPartialThinking((prev) => prev + thought),
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
      )

      setStreamCtrl(ctrl)
    },
    [sessionId],
  )

  const stop = useCallback(() => {
    streamCtrl?.abort()
    setStreaming(false)
    setStreamCtrl(null)
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
  }, [streamCtrl])

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
    async (command: string, sid?: string): Promise<{ text: string; action?: string }> => {
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
    send,
    stop,
    runCommand,
    load,
    addCommandMessage,
    beginEdit,
  }
}

function safeParseArgs(text: string): Record<string, unknown> {
  try {
    const v = JSON.parse(text)
    return typeof v === 'object' && v && !Array.isArray(v) ? v : { value: v }
  } catch {
    return { _raw: text }
  }
}
