import { useState, useEffect, useCallback } from 'react'
import { listSessions, createSession, deleteSession, updateSession } from '../api/sessions'
import type { ChatSession } from '../types'

export function useSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(list)
    } catch {
      // server not ready
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const create = useCallback(async (title?: string) => {
    const s = await createSession({ title })
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    return s
  }, [])

  const remove = useCallback(async (id: string) => {
    await deleteSession(id)
    setSessions((prev) => prev.filter((s) => s.id !== id))
    if (activeId === id) setActiveId(null)
  }, [activeId])

  const rename = useCallback(async (id: string, title: string) => {
    await updateSession(id, { title })
    setSessions((prev) =>
      prev.map((s) => (s.id === id ? { ...s, title } : s)),
    )
  }, [])

  return {
    sessions,
    activeId,
    setActiveId,
    loading,
    refresh,
    create,
    remove,
    rename,
  }
}
