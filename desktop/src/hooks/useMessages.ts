import { useState, useEffect, useCallback, useRef } from 'react'
import { listMessages, streamMessage, executeCommand, editFromMessageApi } from '../api/messages'
import type { ChatMessage } from '../types'

export type ActiveTool = { name: string; status: 'running' | 'done' }

export function useMessages(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([])
  const [streamCtrl, setStreamCtrl] = useState<AbortController | null>(null)
  /** Blocks load() from wiping in-flight optimistic state during session create race. */
  const streamingRef = useRef(false)
  const sendLockRef = useRef(false)

  const load = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      return
    }
    // Don't clobber an in-flight send (common when create() flips sessionId mid-send).
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
      // Prevent double-submit (Enter + button, or rapid re-entry).
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
      setActiveTools([])

      let doneReceived = false

      const finishOk = async () => {
        if (doneReceived) return
        doneReceived = true
        setStreaming(false)
        setStreamCtrl(null)
        setPartialText('')
        setActiveTools([])
        streamingRef.current = false
        sendLockRef.current = false
        // Single source of truth: reload from server (avoids duplicate client+server rows).
        try {
          const msgs = await listMessages(targetId)
          setMessages(msgs)
        } catch {
          // keep optimistic state
        }
      }

      const finishErr = async (errMsg: string) => {
        if (doneReceived) return
        doneReceived = true
        setStreaming(false)
        setStreamCtrl(null)
        setPartialText('')
        setActiveTools([])
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
        undefined,
        (name) => {
          setActiveTools((prev) => {
            if (prev.some((t) => t.name === name && t.status === 'running')) return prev
            return [...prev, { name, status: 'running' }]
          })
        },
        (name) => {
          setActiveTools((prev) =>
            prev.map((t) => (t.name === name ? { ...t, status: 'done' as const } : t)),
          )
        },
        attachments,
      )

      setStreamCtrl(ctrl)
    },
    [sessionId],
  )

  const stop = useCallback(() => {
    streamCtrl?.abort()
    setStreaming(false)
    setStreamCtrl(null)
    streamingRef.current = false
    sendLockRef.current = false
    setPartialText((text) => {
      if (text.trim()) {
        const assistantMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
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
        setMessages((prev) => [...prev, assistantMsg])
      }
      return ''
    })
  }, [streamCtrl])

  /**
   * Edit-and-resend: soft-delete this user message + all after it,
   * return text for the composer (falls back to *fallbackContent* if API omits it).
   */
  const beginEdit = useCallback(
    async (msgId: string, fallbackContent?: string): Promise<string | null> => {
      if (!sessionId || streamingRef.current) return null
      // Always drop local messages from this user msg onward for immediate UI feedback.
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === msgId)
        if (idx < 0) return prev.filter((m) => !m.reverted)
        return prev.slice(0, idx)
      })
      try {
        const r = await editFromMessageApi(sessionId, msgId)
        // Sync from server (reverted msgs are filtered out).
        await load()
        const text =
          typeof r.content === 'string' && r.content.length > 0
            ? r.content
            : (fallbackContent ?? '')
        return text
      } catch (e: unknown) {
        console.warn('Edit failed:', e instanceof Error ? e.message : e)
        // Still return local text so the composer is never blank after Edit.
        return fallbackContent ?? null
      }
    },
    [sessionId, load],
  )

  const runCommand = useCallback(
    async (command: string, sid?: string): Promise<{ text: string; action?: string }> => {
      const targetId = sid || sessionId
      if (!targetId) return { text: 'No session active.' }
      try {
        const r = await executeCommand(targetId, command)
        return { text: r.text, action: r.action }
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
    activeTools,
    send,
    stop,
    runCommand,
    load,
    addCommandMessage,
    beginEdit,
  }
}
