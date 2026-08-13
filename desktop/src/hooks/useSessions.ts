import { useState, useCallback, useRef } from 'react'
import {
  listSessions,
  createSession,
  deleteSession,
  updateSession,
  bulkSetSessionProject,
} from '../api/sessions'
import { updateSettings } from '../api/settings'
import type { ChatSession } from '../types'
import { addKnownProject } from '../utils/sessionProjects'

const PAGE_SIZE = 100

export function useSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const hasLoaded = useRef(false)
  const offsetRef = useRef(0)
  /** Avoid a full GET /settings on every New Session once we know project_path. */
  const projectPathCache = useRef<string | undefined | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const page = await listSessions(PAGE_SIZE, 0)
      offsetRef.current = page.sessions.length
      setSessions(page.sessions)
      setHasMore(page.has_more)
      setActiveId((cur) => {
        if (cur && page.sessions.some((s) => s.id === cur)) return cur
        return page.sessions.length > 0 ? page.sessions[0]!.id : null
      })
    } catch {
      // server not ready
    } finally {
      setLoading(false)
      hasLoaded.current = true
    }
  }, [])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    try {
      const page = await listSessions(PAGE_SIZE, offsetRef.current)
      offsetRef.current += page.sessions.length
      setSessions((prev) => {
        const seen = new Set(prev.map((s) => s.id))
        const extra = page.sessions.filter((s) => !seen.has(s.id))
        return [...prev, ...extra]
      })
      setHasMore(page.has_more && page.sessions.length > 0)
    } catch {
      /* */
    } finally {
      setLoadingMore(false)
    }
  }, [hasMore, loadingMore])

  return {
    sessions,
    activeId,
    setActiveId,
    loading,
    hasMore,
    loadingMore,
    refresh,
    loadMore,
    create: useCallback(
      async (
        title?: string,
        llm?: { provider?: string; model?: string },
      ) => {
        try {
          // New Session = root (no project). Explicit "" so API does not inherit
          // global settings.project_path. Use createInProject to attach a folder.
          const s = await createSession({
            title,
            project_path: '',
            model: llm?.model,
            llm_provider: llm?.provider,
          })
          setSessions((prev) => [s, ...prev])
          setActiveId(s.id)
          return s
        } catch (e: unknown) {
          console.warn(
            '[remedy] createSession failed',
            e instanceof Error ? e.message : e,
          )
          return null
        }
      },
      [],
    ),
    createInProject: useCallback(
      async (
        projectPath: string | null,
        title?: string,
        opts?: {
          setAsDefault?: boolean
          llm?: { provider?: string; model?: string }
        },
      ) => {
        try {
          const project_path =
            projectPath && projectPath.trim() && projectPath.trim() !== '.'
              ? projectPath.trim()
              : ''
          if (project_path) {
            projectPathCache.current = project_path
            addKnownProject(project_path)
            if (opts?.setAsDefault) {
              try {
                await updateSettings({ project_path })
              } catch {
                /* settings optional */
              }
            }
          }
          const s = await createSession({
            title,
            project_path,
            model: opts?.llm?.model,
            llm_provider: opts?.llm?.provider,
          })
          setSessions((prev) => [s, ...prev])
          setActiveId(s.id)
          return s
        } catch {
          return null
        }
      },
      [],
    ),
    setProject: useCallback(async (id: string, projectPath: string | null) => {
      try {
        const project_path =
          projectPath && projectPath.trim() && projectPath.trim() !== '.'
            ? projectPath.trim()
            : ''
        if (project_path) {
          addKnownProject(project_path)
        }
        const s = await updateSession(id, { project_path })
        setSessions((prev) =>
          prev.map((row) =>
            row.id === id
              ? {
                  ...row,
                  project_path: s.project_path ?? (project_path || null),
                }
              : row,
          ),
        )
        return s
      } catch {
        return null
      }
    }, []),
    bulkSetProject: useCallback(async (ids: string[], projectPath: string | null) => {
      if (!ids.length) return null
      try {
        if (projectPath && projectPath.trim() && projectPath.trim() !== '.') {
          addKnownProject(projectPath.trim())
        }
        const res = await bulkSetSessionProject(ids, projectPath)
        const path = res.project_path ?? null
        const updated = new Set(res.updated || [])
        setSessions((prev) =>
          prev.map((row) =>
            updated.has(row.id) ? { ...row, project_path: path } : row,
          ),
        )
        return res
      } catch {
        return null
      }
    }, []),
    clearProjectCache: useCallback(() => {
      projectPathCache.current = null
    }, []),
    remove: useCallback(async (id: string) => {
      // Stop in-flight client/server turn before deleting the session row.
      try {
        const { stopStreamJob } = await import('../sessions/streamJobs')
        await stopStreamJob(id)
      } catch {
        /* */
      }
      await deleteSession(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      setActiveId((cur) => (cur === id ? null : cur))
    }, [setActiveId]),
    rename: useCallback(async (id: string, title: string) => {
      await updateSession(id, { title })
      setSessions((prev) =>
        prev.map((s) => (s.id === id ? { ...s, title } : s)),
      )
    }, []),
  }
}
