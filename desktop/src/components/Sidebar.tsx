import { useMemo, useState, useEffect, useRef, type ReactNode, type DragEvent } from 'react'
import type { ChatSession } from '../types'
import { relativeTime } from '../utils/relativeTime'
import {
  getAllSessionMeta,
  isSessionArchived,
  setSessionMeta,
  toggleSessionArchive,
  toggleSessionPin,
  type SessionMeta,
} from '../utils/sessionMeta'
import {
  addKnownProject,
  getCollapsedProjects,
  getKnownProjects,
  getLockedProjects,
  groupSessionsByProject,
  isNoProjectPath,
  isProjectLocked,
  projectKey,
  removeKnownProject,
  setProjectCollapsed,
  setProjectLocked,
  type ProjectGroup,
} from '../utils/sessionProjects'
import { applySidebarOrder, PINNED_GROUP_KEY } from '../sidebar/orderApply'
import { useSidebarOrder } from '../sidebar/useSidebarOrder'
import { IconEdit } from './icons'
import { OrderButtons } from './OrderButtons'
import { SessionBusyBadge } from './SessionBusyBadge'

interface SidebarProps {
  sessions: ChatSession[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onNewInProject?: (
    projectPath: string | null,
    opts?: { setAsDefault?: boolean },
  ) => void
  onDelete: (id: string) => void
  onRename?: (id: string, title: string) => void
  onSetSessionProject?: (id: string, projectPath: string | null) => void
  /** Bulk-move selected sessions to a project (null = no project). */
  onBulkSetProject?: (ids: string[], projectPath: string | null) => void
  onBrowseProject?: () => Promise<string | null>
  onExport?: (id: string) => void
  onImport?: () => void
  hasMore?: boolean
  loadingMore?: boolean
  onLoadMore?: () => void
  footer?: ReactNode
  /** Open session tabs live only inside the Sessions slide */
  openTabIds?: string[]
  onCloseTab?: (id: string) => void
  /** Fill parent (three-frame workspace slide) instead of fixed 270px column */
  embedded?: boolean
  /** Session ids with a live background or focused turn (Phase A). */
  busySessionIds?: Set<string> | string[]
}

const DRAG_MIME = 'application/x-remedy-session-ids'

export function Sidebar({
  sessions,
  activeId,
  onSelect,
  onNew,
  onNewInProject,
  onDelete,
  onRename,
  onSetSessionProject,
  onBulkSetProject,
  onBrowseProject,
  onExport,
  onImport,
  hasMore,
  loadingMore,
  onLoadMore,
  footer,
  openTabIds = [],
  embedded = false,
  busySessionIds,
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [meta, setMeta] = useState<Record<string, SessionMeta>>(() => getAllSessionMeta())
  const [tagDraft, setTagDraft] = useState('')
  const [tagTarget, setTagTarget] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'pinned' | 'archived'>('all')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const renameRef = useRef<HTMLInputElement>(null)
  const [, setTick] = useState(0)
  const [knownProjects, setKnownProjects] = useState<string[]>(() => getKnownProjects())
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => getCollapsedProjects())
  const [lockedProjects, setLockedProjects] = useState<Set<string>>(() => getLockedProjects())
  const [addingProject, setAddingProject] = useState(false)
  const [addProjectDraft, setAddProjectDraft] = useState('')
  const [moveTarget, setMoveTarget] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [dropHoverKey, setDropHoverKey] = useState<string | null>(null)
  const [setDefaultOnNew, setSetDefaultOnNew] = useState(false)
  const lastClickedRef = useRef<string | null>(null)
  const {
    projectOrder,
    sessionOrderMap,
    moveProject,
    moveSession,
    onSessionRehomed,
  } = useSidebarOrder()
  const busySet = useMemo(() => {
    if (!busySessionIds) return new Set<string>()
    return busySessionIds instanceof Set
      ? busySessionIds
      : new Set(busySessionIds)
  }, [busySessionIds])

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (renamingId) {
      requestAnimationFrame(() => {
        renameRef.current?.focus()
        renameRef.current?.select()
      })
    }
  }, [renamingId])

  // Drop selection when sessions disappear
  useEffect(() => {
    const ids = new Set(sessions.map((s) => s.id))
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [sessions])

  const openIds = useMemo(() => new Set(openTabIds), [openTabIds])

  const groups = useMemo(() => {
    let list = [...sessions]
    if (filter === 'pinned') {
      list = list.filter((s) => meta[s.id]?.pinned)
    } else if (filter === 'archived') {
      list = list.filter((s) =>
        isSessionArchived(s, meta[s.id], { openIds }),
      )
    } else {
      // Hot list: hide archived (manual or auto-age) unless open
      list = list.filter(
        (s) => !isSessionArchived(s, meta[s.id], { openIds }),
      )
    }
    const q = query.trim().toLowerCase()
    if (q) {
      list = list.filter((s) => {
        const title = (s.title || '').toLowerCase()
        const tags = (meta[s.id]?.tags || []).join(' ').toLowerCase()
        const folder = (meta[s.id]?.folder || '').toLowerCase()
        const proj = (s.project_path || '').toLowerCase()
        return (
          title.includes(q)
          || tags.includes(q)
          || folder.includes(q)
          || proj.includes(q)
        )
      })
    }
    list.sort((a, b) => {
      const ap = meta[a.id]?.pinned ? 1 : 0
      const bp = meta[b.id]?.pinned ? 1 : 0
      if (ap !== bp) return bp - ap
      return (b.updated_at || '').localeCompare(a.updated_at || '')
    })
    const grouped = groupSessionsByProject(list, knownProjects)
    const pinnedIds = new Set(
      list.filter((s) => meta[s.id]?.pinned).map((s) => s.id),
    )
    // Pinned filter already shows only stars — skip synthetic strip to avoid empty folders noise.
    const pinStrip = filter !== 'pinned'
    return applySidebarOrder(grouped, projectOrder, sessionOrderMap, pinnedIds, {
      lockedKeys: lockedProjects,
      pinStrip,
    })
  }, [
    sessions,
    query,
    meta,
    filter,
    knownProjects,
    openIds,
    projectOrder,
    sessionOrderMap,
    lockedProjects,
  ])

  const projectKeysInView = useMemo(
    () =>
      groups
        .filter((g) => g.key && g.key !== PINNED_GROUP_KEY)
        .map((g) => g.key),
    // groups is derived above — recompute when groups identity changes
    [groups],
  )

  const refreshMeta = () => setMeta(getAllSessionMeta())

  const commitRename = (id: string) => {
    const next = renameDraft.trim()
    setRenamingId(null)
    if (!next || !onRename) return
    const cur = sessions.find((s) => s.id === id)?.title
    if (next === cur) return
    onRename(id, next)
  }

  const startRename = (s: ChatSession) => {
    if (!onRename) return
    setRenamingId(s.id)
    setRenameDraft(s.title || 'New Session')
  }

  const toggleCollapse = (key: string) => {
    const nextCollapsed = !collapsed[key]
    setProjectCollapsed(key || '__none__', nextCollapsed)
    setCollapsed((prev) => {
      const n = { ...prev }
      if (nextCollapsed) n[key] = true
      else delete n[key]
      return n
    })
  }

  const isCollapsed = (key: string) => Boolean(collapsed[key])

  const handleAddProject = async (path: string) => {
    const trimmed = path.trim()
    if (isNoProjectPath(trimmed)) return
    const next = addKnownProject(trimmed)
    setKnownProjects(next)
    setAddingProject(false)
    setAddProjectDraft('')
    setProjectCollapsed(projectKey(trimmed), false)
    setCollapsed((prev) => {
      const n = { ...prev }
      delete n[projectKey(trimmed)]
      return n
    })
  }

  const [browseError, setBrowseError] = useState('')
  const handleBrowseAdd = async () => {
    if (!onBrowseProject) {
      setBrowseError('Folder picker unavailable')
      return
    }
    setBrowseError('')
    try {
      const path = await onBrowseProject()
      if (path) {
        void handleAddProject(path)
      } else {
        setBrowseError('No folder selected')
      }
    } catch (e: unknown) {
      setBrowseError(e instanceof Error ? e.message : 'Browse failed')
    }
  }

  const projectOptions = useMemo(() => {
    const keys = new Set<string>()
    for (const s of sessions) {
      const k = projectKey(s.project_path)
      if (k) keys.add(k)
    }
    for (const k of knownProjects) keys.add(k)
    return [...keys].sort((a, b) => a.localeCompare(b))
  }, [sessions, knownProjects])

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelected((prev) => {
      const n = new Set(prev)
      if (e.shiftKey && lastClickedRef.current) {
        const flat = groups.flatMap((g) => g.sessions.map((s) => s.id))
        const a = flat.indexOf(lastClickedRef.current)
        const b = flat.indexOf(id)
        if (a >= 0 && b >= 0) {
          const [lo, hi] = a < b ? [a, b] : [b, a]
          for (let i = lo; i <= hi; i++) n.add(flat[i]!)
          return n
        }
      }
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
    lastClickedRef.current = id
  }

  const clearSelection = () => setSelected(new Set())

  const moveSelectedTo = (projectPath: string | null) => {
    const ids = [...selected]
    if (!ids.length) return
    if (onBulkSetProject) onBulkSetProject(ids, projectPath)
    else if (onSetSessionProject) ids.forEach((id) => onSetSessionProject(id, projectPath))
    clearSelection()
  }

  const onDropOnProject = (e: DragEvent, projectPath: string | null) => {
    e.preventDefault()
    setDropHoverKey(null)
    let ids: string[] = []
    try {
      ids = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
    } catch {
      ids = []
    }
    if (!ids.length) return
    for (const id of ids) {
      const from = sessions.find((s) => s.id === id)?.project_path
      onSessionRehomed(id, from, projectPath)
    }
    if (onBulkSetProject) onBulkSetProject(ids, projectPath)
    else if (onSetSessionProject) ids.forEach((id) => onSetSessionProject(id, projectPath))
    clearSelection()
  }

  return (
    <div
      className="sidebar-root flex flex-col min-h-0 h-full"
      style={{
        width: embedded ? '100%' : 270,
        borderColor: 'var(--border)',
        borderRight: embedded ? undefined : '1px solid var(--border)',
      }}
    >
      {/* Sticky chrome: stays visible while session list scrolls */}
      <div className="sidebar-chrome p-3 space-y-2 shrink-0 sticky top-0 z-10">
        <button type="button" onClick={onNew} className="sidebar-new-btn">
          + New session
        </button>
        {(onExport || onImport) && (
          <div className="flex gap-1.5">
            {onImport && (
              <button
                type="button"
                onClick={onImport}
                className="flex-1 px-2 py-1.5 rounded-lg text-xs font-medium"
                style={{
                  background: 'color-mix(in srgb, var(--bg-primary) 88%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
                  color: 'var(--text-secondary)',
                }}
              >
                Import
              </button>
            )}
            {onExport && (
              <button
                type="button"
                onClick={() => activeId && onExport(activeId)}
                disabled={!activeId}
                className="flex-1 px-2 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40"
                style={{
                  background: 'color-mix(in srgb, var(--bg-primary) 88%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
                  color: 'var(--text-secondary)',
                }}
              >
                Export
              </button>
            )}
          </div>
        )}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search sessions, projects…"
          className="sidebar-search"
          aria-label="Search sessions"
        />
        <div className="flex flex-wrap gap-1">
          <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} label="All" />
          <FilterChip
            active={filter === 'pinned'}
            onClick={() => setFilter('pinned')}
            label="★ Pin"
          />
          <FilterChip
            active={filter === 'archived'}
            onClick={() => setFilter('archived')}
            label="Archive"
          />
        </div>
        <label
          className="flex items-center gap-1.5 text-[10px] cursor-pointer"
          style={{ color: 'var(--text-muted)' }}
          title="When creating a session under a project, also save that folder as Settings default project"
        >
          <input
            type="checkbox"
            checked={setDefaultOnNew}
            onChange={(e) => setSetDefaultOnNew(e.target.checked)}
          />
          New-in-project sets default
        </label>
        {/* Project browser — sticky above scrolling sessions */}
        <div className="pt-0.5">
          {!addingProject ? (
            <button
              type="button"
              className="w-full text-left px-2 py-1.5 rounded-md text-xs font-medium"
              style={{
                background: 'var(--bg-primary)',
                border: '1px dashed var(--border)',
                color: 'var(--text-secondary)',
              }}
              onClick={() => setAddingProject(true)}
            >
              + Add project folder
            </button>
          ) : (
            <div
              className="rounded-md p-2 space-y-1.5"
              style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)' }}
            >
              <input
                value={addProjectDraft}
                onChange={(e) => setAddProjectDraft(e.target.value)}
                placeholder="C:\Users\…\MyProject"
                className="w-full rounded px-1.5 py-1 text-[11px] outline-none"
                style={{
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                }}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && addProjectDraft.trim()) {
                    void handleAddProject(addProjectDraft)
                  }
                  if (e.key === 'Escape') {
                    setAddingProject(false)
                    setAddProjectDraft('')
                  }
                }}
              />
              <div className="flex gap-1">
                {onBrowseProject && (
                  <button
                    type="button"
                    className="flex-1 px-1.5 py-1 rounded text-[10px]"
                    style={{
                      background: 'var(--bg-tertiary)',
                      border: '1px solid var(--border)',
                    }}
                    onClick={() => void handleBrowseAdd()}
                  >
                    Browse…
                  </button>
                )}
                <button
                  type="button"
                  className="flex-1 px-1.5 py-1 rounded text-[10px] font-medium"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                  disabled={!addProjectDraft.trim()}
                  onClick={() => void handleAddProject(addProjectDraft)}
                >
                  Add
                </button>
                <button
                  type="button"
                  className="px-1.5 py-1 text-[10px]"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => {
                    setAddingProject(false)
                    setAddProjectDraft('')
                    setBrowseError('')
                  }}
                >
                  Cancel
                </button>
              </div>
              {browseError && (
                <div className="text-[10px]" style={{ color: 'var(--error)' }}>
                  {browseError}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Multi-select action bar */}
      {selected.size > 0 && (
        <div
          className="px-2 py-1.5 border-b flex flex-wrap items-center gap-1 shrink-0"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}
        >
          <span className="text-[10px] font-semibold" style={{ color: 'var(--text-primary)' }}>
            {selected.size} selected
          </span>
          <button
            type="button"
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
            onClick={() => moveSelectedTo(null)}
          >
            → No project
          </button>
          {projectOptions.slice(0, 4).map((p) => (
            <button
              key={p}
              type="button"
              className="text-[10px] px-1.5 py-0.5 rounded truncate max-w-[5.5rem]"
              style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
              title={p}
              onClick={() => moveSelectedTo(p)}
            >
              → {p.split(/[/\\]/).filter(Boolean).pop()}
            </button>
          ))}
          <button
            type="button"
            className="text-[10px] ml-auto"
            style={{ color: 'var(--text-muted)' }}
            onClick={clearSelection}
          >
            Clear
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {groups.map((group) => {
          const projectOnlyKeys = projectKeysInView
          const projectIndex = group.key ? projectOnlyKeys.indexOf(group.key) : -1
          // Manual order only among non-pinned for ↑↓ (matches applySessionOrder).
          const movableSessionIds = group.sessions
            .filter((s) => !meta[s.id]?.pinned)
            .map((s) => s.id)
          return (
          <ProjectSection
            key={group.key || '__none__'}
            group={group}
            collapsed={isCollapsed(group.key)}
            onToggle={() => toggleCollapse(group.key)}
            activeId={activeId}
            selected={selected}
            dropHover={dropHoverKey === (group.key || '__none__')}
            meta={meta}
            renamingId={renamingId}
            renameDraft={renameDraft}
            renameRef={renameRef}
            tagTarget={tagTarget}
            tagDraft={tagDraft}
            moveTarget={moveTarget}
            projectOptions={projectOptions}
            busySet={busySet}
            onSelect={onSelect}
            onToggleSelect={toggleSelect}
            onDelete={onDelete}
            onRename={onRename}
            onStartRename={startRename}
            onCommitRename={commitRename}
            setRenameDraft={setRenameDraft}
            setRenamingId={setRenamingId}
            setTagTarget={setTagTarget}
            setTagDraft={setTagDraft}
            setMoveTarget={setMoveTarget}
            refreshMeta={refreshMeta}
            isPinnedStrip={group.key === PINNED_GROUP_KEY}
            projectLocked={Boolean(
              group.key
              && group.key !== PINNED_GROUP_KEY
              && lockedProjects.has(group.key),
            )}
            onToggleProjectLock={
              group.key && group.key !== PINNED_GROUP_KEY
                ? () => {
                    const next = setProjectLocked(
                      group.key,
                      !isProjectLocked(group.key),
                    )
                    setLockedProjects(new Set(next))
                  }
                : undefined
            }
            projectDisableUp={
              !group.key
              || group.key === PINNED_GROUP_KEY
              || lockedProjects.has(group.key)
              || projectIndex <= 0
            }
            projectDisableDown={
              !group.key
              || group.key === PINNED_GROUP_KEY
              || lockedProjects.has(group.key)
              || projectIndex < 0
              || projectIndex >= projectOnlyKeys.length - 1
            }
            onMoveProjectUp={
              group.key
              && group.key !== PINNED_GROUP_KEY
              && !lockedProjects.has(group.key)
                ? () => moveProject(group.key, 'up', projectOnlyKeys)
                : undefined
            }
            onMoveProjectDown={
              group.key
              && group.key !== PINNED_GROUP_KEY
              && !lockedProjects.has(group.key)
                ? () => moveProject(group.key, 'down', projectOnlyKeys)
                : undefined
            }
            onMoveSessionUp={(id) =>
              moveSession(id, group.path || null, 'up', movableSessionIds)
            }
            onMoveSessionDown={(id) =>
              moveSession(id, group.path || null, 'down', movableSessionIds)
            }
            movableSessionIds={movableSessionIds}
            onNewInProject={
              onNewInProject
                ? (path) =>
                    onNewInProject(path, {
                      setAsDefault: setDefaultOnNew && Boolean(path),
                    })
                : undefined
            }
            onSetSessionProject={(id, path) => {
              const from = sessions.find((s) => s.id === id)?.project_path
              onSessionRehomed(id, from, path)
              onSetSessionProject?.(id, path)
            }}
            onDragOverProject={(e) => {
              e.preventDefault()
              e.dataTransfer.dropEffect = 'move'
              setDropHoverKey(group.key || '__none__')
            }}
            onDragLeaveProject={() => setDropHoverKey(null)}
            onDropProject={(e) => onDropOnProject(e, group.key ? group.path : null)}
            onRemoveKnownProject={
              group.key
              && group.key !== PINNED_GROUP_KEY
              && !lockedProjects.has(group.key)
                ? () => setKnownProjects(removeKnownProject(group.path))
                : undefined
            }
          />
          )
        })}

        {hasMore && onLoadMore && (
          <div className="px-2 mb-2">
            <button
              type="button"
              className="w-full py-1.5 rounded text-xs"
              style={{
                background: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                color: 'var(--text-secondary)',
              }}
              disabled={loadingMore}
              onClick={() => onLoadMore()}
            >
              {loadingMore ? 'Loading…' : 'Load more sessions'}
            </button>
          </div>
        )}

        {groups.every((g) => g.sessions.length === 0) && sessions.length === 0 && (
          <div className="px-3 py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
            No sessions yet
          </div>
        )}
      </div>
      <div
        className="px-3 py-1 text-[10px] border-t shrink-0"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        ↑↓ reorder · 📁 move · Archive / pin on hover
      </div>
      {footer}
    </div>
  )
}

function ProjectSection({
  group,
  collapsed,
  onToggle,
  activeId,
  selected,
  dropHover,
  meta,
  renamingId,
  renameDraft,
  renameRef,
  tagTarget,
  tagDraft,
  moveTarget,
  projectOptions,
  busySet,
  onSelect,
  onToggleSelect,
  onDelete,
  onRename,
  onStartRename,
  onCommitRename,
  setRenameDraft,
  setRenamingId,
  setTagTarget,
  setTagDraft,
  setMoveTarget,
  refreshMeta,
  isPinnedStrip,
  projectLocked,
  onToggleProjectLock,
  projectDisableUp,
  projectDisableDown,
  onMoveProjectUp,
  onMoveProjectDown,
  onMoveSessionUp,
  onMoveSessionDown,
  movableSessionIds,
  onNewInProject,
  onSetSessionProject,
  onDragOverProject,
  onDragLeaveProject,
  onDropProject,
  onRemoveKnownProject,
}: {
  group: ProjectGroup
  collapsed: boolean
  onToggle: () => void
  activeId: string | null
  selected: Set<string>
  dropHover: boolean
  meta: Record<string, SessionMeta>
  renamingId: string | null
  renameDraft: string
  renameRef: React.RefObject<HTMLInputElement | null>
  tagTarget: string | null
  tagDraft: string
  moveTarget: string | null
  projectOptions: string[]
  busySet: Set<string>
  onSelect: (id: string) => void
  onToggleSelect: (id: string, e: React.MouseEvent) => void
  onDelete: (id: string) => void
  onRename?: (id: string, title: string) => void
  onStartRename: (s: ChatSession) => void
  onCommitRename: (id: string) => void
  setRenameDraft: (v: string) => void
  setRenamingId: (v: string | null) => void
  setTagTarget: (v: string | null) => void
  setTagDraft: (v: string) => void
  setMoveTarget: (v: string | null) => void
  refreshMeta: () => void
  isPinnedStrip?: boolean
  projectLocked?: boolean
  onToggleProjectLock?: () => void
  projectDisableUp?: boolean
  projectDisableDown?: boolean
  onMoveProjectUp?: () => void
  onMoveProjectDown?: () => void
  onMoveSessionUp?: (id: string) => void
  onMoveSessionDown?: (id: string) => void
  movableSessionIds?: string[]
  onNewInProject?: (projectPath: string | null) => void
  onSetSessionProject?: (id: string, projectPath: string | null) => void
  onDragOverProject: (e: DragEvent) => void
  onDragLeaveProject: () => void
  onDropProject: (e: DragEvent) => void
  onRemoveKnownProject?: () => void
}) {
  const isNone = !group.key && !isPinnedStrip
  const count = group.sessions.length
  const hasActive = group.sessions.some((s) => s.id === activeId)
  const locked = Boolean(projectLocked)

  return (
    <div className="mb-0.5">
      <div
        className="sidebar-project-header group/header flex items-center gap-1 px-2 py-1.5 mx-1 cursor-pointer select-none"
        style={{
          background: dropHover
            ? 'color-mix(in srgb, var(--accent) 28%, transparent)'
            : hasActive && !isNone
              ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
              : isPinnedStrip
                ? 'color-mix(in srgb, var(--accent) 8%, transparent)'
                : 'transparent',
          outline: dropHover ? '1px dashed var(--accent)' : 'none',
          color: 'var(--text-secondary)',
        }}
        onClick={onToggle}
        onDragOver={isPinnedStrip ? undefined : onDragOverProject}
        onDragLeave={isPinnedStrip ? undefined : onDragLeaveProject}
        onDrop={isPinnedStrip ? undefined : onDropProject}
      >
        <span
          className="text-[10px] w-3 text-center"
          style={{ color: 'var(--text-muted)' }}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '▸' : '▾'}
        </span>
        <span className="text-[12px]">
          {isPinnedStrip ? '★' : isNone ? '○' : locked ? '🔒' : '📁'}
        </span>
        <span
          className="truncate flex-1 min-w-0 text-xs font-semibold"
          style={{ color: 'var(--text-primary)' }}
          title={
            isPinnedStrip
              ? 'Pinned sessions (always at top)'
              : group.path
                ? group.path
                : 'Sessions without a project'
          }
        >
          {group.label}
        </span>
        <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {count}
        </span>
        {onToggleProjectLock && (
          <button
            type="button"
            className={
              locked
                ? 'text-[11px] w-4 opacity-100'
                : 'text-[11px] w-4 opacity-0 group-hover/header:opacity-100'
            }
            style={{ color: locked ? 'var(--accent)' : 'var(--text-muted)' }}
            title={
              locked
                ? 'Unlock folder (allows reorder and remove)'
                : 'Lock folder (prevents reorder and remove)'
            }
            aria-label={locked ? 'Unlock project folder' : 'Lock project folder'}
            aria-pressed={locked}
            onClick={(e) => {
              e.stopPropagation()
              onToggleProjectLock()
            }}
          >
            {locked ? '🔒' : '🔓'}
          </button>
        )}
        {onMoveProjectUp && onMoveProjectDown && !locked && (
          <OrderButtons
            onUp={onMoveProjectUp}
            onDown={onMoveProjectDown}
            disableUp={projectDisableUp}
            disableDown={projectDisableDown}
            titleUp="Move project folder up"
            titleDown="Move project folder down"
          />
        )}
        {onNewInProject && !isPinnedStrip && (
          <button
            type="button"
            className="opacity-0 group-hover/header:opacity-100 text-[10px] px-1 rounded"
            style={{ color: 'var(--accent)' }}
            title="New session in this project"
            onClick={(e) => {
              e.stopPropagation()
              onNewInProject(isNone ? null : group.path)
            }}
          >
            +
          </button>
        )}
        {onRemoveKnownProject && count === 0 && !locked && !isPinnedStrip && (
          <button
            type="button"
            className="opacity-0 group-hover/header:opacity-100 text-[10px] w-4"
            style={{ color: 'var(--error)' }}
            onClick={(e) => {
              e.stopPropagation()
              onRemoveKnownProject()
            }}
          >
            ×
          </button>
        )}
      </div>

      {!collapsed && (
        <div className={isNone ? '' : 'ml-2 border-l'} style={{ borderColor: 'var(--border)' }}>
          {group.sessions.map((s) => {
            const m = meta[s.id] || {}
            const pinned = Boolean(m.pinned)
            const isRenaming = renamingId === s.id
            const isSel = selected.has(s.id)
            return (
              <div
                key={s.id}
                // Drag-to-folder is unreliable in Tauri WebView — reorder via ↑↓.
                draggable={false}
                className={`sidebar-session-row group flex flex-col px-2 cursor-pointer text-sm relative${
                  s.id === activeId ? ' is-active' : ''
                }${isSel ? ' is-selected' : ''}`}
                style={{
                  background: 'transparent',
                  color: s.id === activeId ? 'var(--text-primary)' : 'var(--text-secondary)',
                  paddingTop: 'var(--sidebar-row-py)',
                  paddingBottom: 'var(--sidebar-row-py)',
                  marginLeft: isNone ? 0 : 4,
                }}
                onClick={() => {
                  if (!isRenaming) onSelect(s.id)
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation()
                  onStartRename(s)
                }}
              >
                <div className="flex items-center gap-0.5 px-1 min-w-0">
                  <input
                    type="checkbox"
                    checked={isSel}
                    onChange={() => {}}
                    onClick={(e) => onToggleSelect(s.id, e)}
                    className="flex-shrink-0 w-3 h-3 cursor-pointer"
                    title="Select for bulk move"
                    aria-label={`Select ${s.title || 'session'}`}
                  />
                  <button
                    type="button"
                    className="flex-shrink-0 text-[10px] w-3.5 opacity-50 group-hover:opacity-100"
                    style={{ color: pinned ? 'var(--accent)' : 'var(--text-muted)' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleSessionPin(s.id)
                      refreshMeta()
                    }}
                    title={pinned ? 'Unpin' : 'Pin'}
                  >
                    {pinned ? '★' : '☆'}
                  </button>
                  {isRenaming ? (
                    <input
                      ref={renameRef}
                      value={renameDraft}
                      onChange={(e) => setRenameDraft(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onBlur={() => onCommitRename(s.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          onCommitRename(s.id)
                        }
                        if (e.key === 'Escape') {
                          e.preventDefault()
                          setRenamingId(null)
                        }
                      }}
                      className="flex-1 min-w-0 rounded px-1 py-0.5 text-xs outline-none"
                      style={{
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--accent)',
                        color: 'var(--text-primary)',
                      }}
                    />
                  ) : (
                    <span
                      className="truncate flex-1 min-w-0 text-[13px] font-medium leading-snug flex items-center gap-1"
                      title={s.title || 'New Session'}
                    >
                      {s.origin_channel && (
                        <span
                          className="flex-shrink-0 text-[9px] px-1 rounded"
                          style={{
                            background: 'color-mix(in srgb, var(--accent) 22%, transparent)',
                            color: 'var(--accent)',
                          }}
                          title={`From ${s.origin_channel}`}
                        >
                          {s.origin_channel === 'telegram'
                            ? 'TG'
                            : s.origin_channel === 'discord'
                              ? 'DC'
                              : s.origin_channel === 'slack'
                                ? 'SL'
                                : s.origin_channel === 'mattermost'
                                  ? 'MM'
                                  : s.origin_channel === 'whatsapp'
                                    ? 'WA'
                                    : s.origin_channel.slice(0, 2).toUpperCase()}
                        </span>
                      )}
                      {busySet.has(s.id) && <SessionBusyBadge />}
                      <span className="truncate">{s.title || 'New Session'}</span>
                    </span>
                  )}
                  {!pinned && onMoveSessionUp && onMoveSessionDown && (
                    <OrderButtons
                      onUp={() => onMoveSessionUp(s.id)}
                      onDown={() => onMoveSessionDown(s.id)}
                      disableUp={
                        !movableSessionIds?.length
                        || movableSessionIds.indexOf(s.id) <= 0
                      }
                      disableDown={
                        !movableSessionIds?.length
                        || movableSessionIds.indexOf(s.id)
                          >= movableSessionIds.length - 1
                      }
                      titleUp="Move session up"
                      titleDown="Move session down"
                    />
                  )}
                  {onRename && !isRenaming && (
                    <button
                      type="button"
                      className="opacity-0 group-hover:opacity-80 p-0.5 shrink-0"
                      style={{ color: 'var(--text-muted)' }}
                      onClick={(e) => {
                        e.stopPropagation()
                        onStartRename(s)
                      }}
                    >
                      <IconEdit size={11} />
                    </button>
                  )}
                  <button
                    type="button"
                    className={`text-[10px] shrink-0 px-1 rounded ${
                      m.archived ? 'opacity-100' : 'opacity-0 group-hover:opacity-90'
                    }`}
                    style={{
                      color: m.archived ? 'var(--accent)' : 'var(--text-muted)',
                      border: '1px solid var(--border)',
                      background: m.archived
                        ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
                        : 'transparent',
                    }}
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleSessionArchive(s.id)
                      refreshMeta()
                    }}
                    title={m.archived ? 'Unarchive session' : 'Archive session'}
                    aria-label={m.archived ? 'Unarchive session' : 'Archive session'}
                  >
                    {m.archived ? 'Unarchive' : 'Archive'}
                  </button>
                  <button
                    className="w-4 h-4 opacity-0 group-hover:opacity-70 shrink-0 text-[11px]"
                    style={{ color: 'var(--error)' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      const title = (s.title || 'this chat').trim()
                      const ok = window.confirm(
                        `Delete “${title}”?\n\n`
                          + 'This removes the transcript plus session notes, attachments, '
                          + 'plans, and undo history for this chat.\n\n'
                          + 'Partner Memory (facts Remedy remembers about you) is kept. '
                          + 'Use Settings → You & Agent → Wipe persona to forget those.',
                      )
                      if (ok) onDelete(s.id)
                    }}
                  >
                    ×
                  </button>
                </div>
                <div className="flex items-center gap-1.5 px-1 mt-0.5 min-h-[0.9rem] pl-7 min-w-0">
                  <span
                    className="text-[9px] tabular-nums shrink-0"
                    style={{ color: 'var(--text-muted)', opacity: 0.85 }}
                    title={s.updated_at || undefined}
                  >
                    {relativeTime(s.updated_at)}
                  </span>
                  <span className="text-[9px] shrink-0" style={{ color: 'var(--text-muted)' }}>
                    ·
                  </span>
                  <span className="text-[9px] shrink-0" style={{ color: 'var(--text-muted)' }}>
                    {s.message_count} msg
                  </span>
                  {(m.tags || []).slice(0, 2).map((t) => (
                    <span
                      key={t}
                      className="text-[10px] px-1 rounded"
                      style={{
                        background: 'color-mix(in srgb, var(--accent) 15%, transparent)',
                        color: 'var(--accent)',
                      }}
                    >
                      {t}
                    </span>
                  ))}
                  <button
                    type="button"
                    className="text-[10px] ml-auto opacity-0 group-hover:opacity-70"
                    style={{ color: 'var(--text-muted)' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      setTagTarget(tagTarget === s.id ? null : s.id)
                      setMoveTarget(null)
                      setTagDraft('')
                    }}
                  >
                    +tag
                  </button>
                  {onSetSessionProject && (
                    <button
                      type="button"
                      className="text-[10px] opacity-0 group-hover:opacity-70"
                      style={{ color: 'var(--text-muted)' }}
                      onClick={(e) => {
                        e.stopPropagation()
                        setMoveTarget(moveTarget === s.id ? null : s.id)
                        setTagTarget(null)
                      }}
                    >
                      📁
                    </button>
                  )}
                </div>
                {tagTarget === s.id && (
                  <div className="px-1 mt-1 flex gap-1 pl-5" onClick={(e) => e.stopPropagation()}>
                    <input
                      value={tagDraft}
                      onChange={(e) => setTagDraft(e.target.value)}
                      placeholder="tag name"
                      className="flex-1 rounded px-1.5 py-0.5 text-[10px] outline-none"
                      style={{
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border)',
                        color: 'var(--text-primary)',
                      }}
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && tagDraft.trim()) {
                          const tags = [
                            ...(getAllSessionMeta()[s.id]?.tags || []),
                            tagDraft.trim(),
                          ]
                          setSessionMeta(s.id, { tags })
                          refreshMeta()
                          setTagDraft('')
                          setTagTarget(null)
                        }
                        if (e.key === 'Escape') setTagTarget(null)
                      }}
                    />
                  </div>
                )}
                {moveTarget === s.id && onSetSessionProject && (
                  <div className="px-1 mt-1 space-y-0.5 pl-5" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="w-full text-left text-[10px] px-1.5 py-0.5 rounded"
                      onClick={() => {
                        onSetSessionProject(s.id, null)
                        setMoveTarget(null)
                      }}
                    >
                      ○ No project
                    </button>
                    {projectOptions.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className="w-full text-left text-[10px] px-1.5 py-0.5 rounded truncate"
                        title={p}
                        onClick={() => {
                          onSetSessionProject(s.id, p)
                          setMoveTarget(null)
                        }}
                      >
                        📁 {p.split(/[/\\]/).filter(Boolean).pop() || p}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          {group.sessions.length === 0 && (
            <div
              className="px-3 py-1.5 text-[10px]"
              style={{ color: 'var(--text-muted)', marginLeft: isNone ? 0 : 4 }}
            >
              {isNone ? 'No unattached sessions' : 'Empty — use + or 📁 on a session'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`sidebar-filter-chip${active ? ' is-active' : ''}`}
      aria-pressed={active}
    >
      {label}
    </button>
  )
}
