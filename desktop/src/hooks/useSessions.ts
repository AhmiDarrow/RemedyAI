import { useState, useCallback, useRef } from 'react'
import { listSessions, createSession, deleteSession, updateSession } from '../api/sessions'
import { getSettings } from '../api/settings'
import type { ChatSession } from '../types'

export function useSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const hasLoaded = useRef(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const list = await listSessions()
      setSessions(list)
      if (list.length > 0 && !activeId) {
        setActiveId(list[0].id)
      }
    } catch {
      // server not ready
    } finally {
      setLoading(false)
      hasLoaded.current = true
    }
  }, [activeId])

  return {
    sessions,
    activeId,
    setActiveId,
    loading,
    refresh,
    create: useCallback(async (title?: string) => {
      try {
        // Stamp session with the configured default project folder.
        let project_path: string | undefined
        try {
          const s = await getSettings()
          if (s.project_path && s.project_path !== '.') {
            project_path = s.project_path
          }
        } catch {
          // server may omit; create still works — API also inherits from config
        }
        const s = await createSession({ title, project_path })
        setSessions((prev) => [s, ...prev])
        setActiveId(s.id)
        return s
      } catch {
        return null
      }
    }, []),
    remove: useCallback(async (id: string) => {
      await deleteSession(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (activeId === id) setActiveId(null)
    }, [activeId]),
    rename: useCallback(async (id: string, title: string) => {
      await updateSession(id, { title })
      setSessions((prev) =>
        prev.map((s) => (s.id === id ? { ...s, title } : s)),
      )
    }, []),
  }
}
