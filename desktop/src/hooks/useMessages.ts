import { useState, useEffect, useCallback, useRef } from 'react'
import { listMessages, streamMessage, executeCommand, editFromMessageApi } from '../api/messages'
import type { ChatMessage } from '../types'

export function useMessages(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [partialText, setPartialText] = useState('')
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
    async (text: string, model?: string, sid?: string) => {
      const targetId = sid || sessionId
      if (!targetId || !text.trim()) return
      // Prevent double-submit (Enter + button, or rapid re-entry).
      if (sendLockRef.current || streamingRef.current) return
      sendLockRef.current = true
      streamingRef.current = true

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
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

      let doneReceived = false

      const finishOk = async () => {
        if (doneReceived) return
        doneReceived = true
        setStreaming(false)
        setStreamCtrl(null)
        setPartialText('')
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
        text,
        (token) => setPartialText((prev) => prev + token),
        () => {
          void finishOk()
        },
        (errMsg) => {
          void finishErr(errMsg)
        },
        model,
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
   * return text for the composer.
   */
  const beginEdit = useCallback(
    async (msgId: string): Promise<string | null> => {
      if (!sessionId || streamingRef.current) return null
      try {
        const r = await editFromMessageApi(sessionId, msgId)
        // Drop local messages from this user msg onward immediately.
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.id === msgId)
          if (idx < 0) return prev.filter((m) => !m.reverted)
          return prev.slice(0, idx)
        })
        // Sync from server (reverted msgs are filtered out).
        await load()
        return r.content
      } catch (e: unknown) {
        console.warn('Edit failed:', e instanceof Error ? e.message : e)
        return null
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
    send,
    stop,
    runCommand,
    load,
    addCommandMessage,
    beginEdit,
  }
}
