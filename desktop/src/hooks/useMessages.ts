import { useState, useEffect, useCallback } from 'react'
import { listMessages, streamMessage, executeCommand } from '../api/messages'
import type { ChatMessage } from '../types'

export function useMessages(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [streamCtrl, setStreamCtrl] = useState<AbortController | null>(null)

  const load = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      return
    }
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
      if (!targetId) return

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

      const ctrl = streamMessage(
        targetId,
        text,
        (token) => setPartialText((prev) => prev + token),
        (data) => {
          setStreaming(false)
          setStreamCtrl(null)
          setPartialText((t) => {
            if (t.trim()) {
              const assistantMsg: ChatMessage = {
                id: data.request_id || crypto.randomUUID(),
                role: 'assistant',
                content: t,
                thinking: null,
                tool_calls: [],
                tool_results: [],
                model: model || null,
                agent: null,
                tokens: null,
                created_at: new Date().toISOString(),
                reverted: false,
              }
              setMessages((prev) => [...prev, assistantMsg])
            }
            return ''
          })
        },
        (errMsg) => {
          setStreaming(false)
          setStreamCtrl(null)
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
          setPartialText('')
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

  return { messages, loading, streaming, partialText, send, stop, runCommand, load }
}
