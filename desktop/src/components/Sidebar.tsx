import { useMemo, useState, useEffect, useRef, type ReactNode, type DragEvent } from 'react'
import type { ChatSession } from '../types'
import { relativeTime } from '../utils/relativeTime'
import {
  getAllSessionMeta,
  setSessionMeta,
  toggleSessionPin,
  type SessionMeta,
} from '../utils/sessionMeta'
import {
  addKnownProject,
  getCollapsedProjects,
  getKnownProjects,
  groupSessionsByProject,
  isNoProjectPath,
  projectKey,
  removeKnownProject,
  setProjectCollapsed,
  type ProjectGroup,
} from '../utils/sessionProjects'
import { IconEdit } from './icons'

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
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [meta, setMeta] = useState<Record<string, SessionMeta>>(() => getAllSessionMeta())
  const [tagDraft, setTagDraft] = useState('')
  const [tagTarget, setTagTarget] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'pinned'>('all')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const renameRef = useRef<HTMLInputElement>(null)
  const [, setTick] = useState(0)
  const [knownProjects, setKnownProjects] = useState<string[]>(() => getKnownProjects())
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => getCollapsedProjects())
  const [addingProject, setAddingProject] = useState(false)
  const [addProjectDraft, setAddProjectDraft] = useState('')
  const [moveTarget, setMoveTarget] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [dropHoverKey, setDropHoverKey] = useState<string | null>(null)
  const [setDefaultOnNew, setSetDefaultOnNew] = useState(false)
  const lastClickedRef = useRef<string | null>(null)

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

  const groups = useMemo(() => {
    let list = [...sessions]
    if (filter === 'pinned') {
      list = list.filter((s) => meta[s.id]?.pinned)
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
    return groupSessionsByProject(list, knownProjects)
  }, [sessions, query, meta, filter, knownProjects])

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

  const handleBrowseAdd = async () => {
    if (!onBrowseProject) return
    const path = await onBrowseProject()
    if (path) void handleAddProject(path)
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

  const onDragStartSession = (e: DragEvent, id: string) => {
    const ids = selected.has(id) && selected.size > 0 ? [...selected] : [id]
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(ids))
    e.dataTransfer.effectAllowed = 'move'
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
    if (onBulkSetProject) onBulkSetProject(ids, projectPath)
    else if (onSetSessionProject) ids.forEach((id) => onSetSessionProject(id, projectPath))
    clearSelection()
  }

  return (
    <div
      className="flex flex-col border-r"
      style={{
        width: 270,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
      }}
    >
      <div className="p-3 border-b space-y-2" style={{ borderColor: 'var(--border)' }}>
        <button
          onClick={onNew}
          className="w-full text-left px-3 py-2 rounded-md text-sm font-medium transition-colors"
          style={{ background: 'var(--accent)', color: '#fff' }}
          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-hover)')}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--accent)')}
        >
          + New Session
        </button>
        {(onExport || onImport) && (
          <div className="flex gap-1.5">
            {onImport && (
              <button
                type="button"
                onClick={onImport}
                className="flex-1 px-2 py-1.5 rounded-md text-xs font-medium"
                style={{
                  background: 'var(--bg-primary)',
                  border: '1px solid var(--border)',
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
                className="flex-1 px-2 py-1.5 rounded-md text-xs font-medium disabled:opacity-40"
                style={{
                  background: 'var(--bg-primary)',
                  border: '1px solid var(--border)',
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
          className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          aria-label="Search sessions"
        />
        <div className="flex flex-wrap gap-1">
          <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} label="All" />
          <FilterChip
            active={filter === 'pinned'}
            onClick={() => setFilter('pinned')}
            label="★ Pin"
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
      </div>

      {/* Multi-select action bar */}
      {selected.size > 0 && (
        <div
          className="px-2 py-1.5 border-b flex flex-wrap items-center gap-1"
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

      <div className="flex-1 overflow-y-auto py-1">
        {groups.map((group) => (
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
            onNewInProject={
              onNewInProject
                ? (path) =>
                    onNewInProject(path, {
                      setAsDefault: setDefaultOnNew && Boolean(path),
                    })
                : undefined
            }
            onSetSessionProject={onSetSessionProject}
            onDragStartSession={onDragStartSession}
            onDragOverProject={(e) => {
              e.preventDefault()
              e.dataTransfer.dropEffect = 'move'
              setDropHoverKey(group.key || '__none__')
            }}
            onDragLeaveProject={() => setDropHoverKey(null)}
            onDropProject={(e) => onDropOnProject(e, group.key ? group.path : null)}
            onRemoveKnownProject={
              group.key
                ? () => setKnownProjects(removeKnownProject(group.path))
                : undefined
            }
          />
        ))}

        <div className="px-2 mt-2 mb-2">
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
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

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
        Checkbox multi-select · drag onto folders · Shift+click range
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
  onNewInProject,
  onSetSessionProject,
  onDragStartSession,
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
  onNewInProject?: (projectPath: string | null) => void
  onSetSessionProject?: (id: string, projectPath: string | null) => void
  onDragStartSession: (e: DragEvent, id: string) => void
  onDragOverProject: (e: DragEvent) => void
  onDragLeaveProject: () => void
  onDropProject: (e: DragEvent) => void
  onRemoveKnownProject?: () => void
}) {
  const isNone = !group.key
  const count = group.sessions.length
  const hasActive = group.sessions.some((s) => s.id === activeId)

  return (
    <div className="mb-0.5">
      <div
        className="group/header flex items-center gap-1 px-2 py-1.5 mx-1 rounded-md cursor-pointer select-none"
        style={{
          background: dropHover
            ? 'color-mix(in srgb, var(--accent) 28%, transparent)'
            : hasActive && !isNone
              ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
              : 'transparent',
          outline: dropHover ? '1px dashed var(--accent)' : 'none',
          color: 'var(--text-secondary)',
        }}
        onClick={onToggle}
        onDragOver={onDragOverProject}
        onDragLeave={onDragLeaveProject}
        onDrop={onDropProject}
        title={
          group.path
            ? `${group.path} — drop sessions here`
            : 'Sessions without a project — drop to clear project'
        }
      >
        <span className="text-[10px] w-3 text-center" style={{ color: 'var(--text-muted)' }}>
          {collapsed ? '▸' : '▾'}
        </span>
        <span className="text-[12px]">{isNone ? '○' : '📁'}</span>
        <span
          className="truncate flex-1 min-w-0 text-xs font-semibold"
          style={{ color: 'var(--text-primary)' }}
        >
          {group.label}
        </span>
        <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {count}
        </span>
        {onNewInProject && (
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
        {onRemoveKnownProject && count === 0 && (
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
                draggable={!isRenaming}
                onDragStart={(e) => onDragStartSession(e, s.id)}
                className="group flex flex-col px-2 cursor-pointer text-sm relative"
                style={{
                  background: isSel
                    ? 'color-mix(in srgb, var(--accent) 18%, var(--bg-tertiary))'
                    : s.id === activeId
                      ? 'var(--bg-tertiary)'
                      : 'transparent',
                  color: s.id === activeId ? 'var(--text-primary)' : 'var(--text-secondary)',
                  borderLeft:
                    s.id === activeId ? '3px solid var(--accent)' : '3px solid transparent',
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
                <div className="flex items-center gap-1 px-1">
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
                    className="flex-shrink-0 text-xs w-4 opacity-50 group-hover:opacity-100"
                    style={{ color: pinned ? 'var(--accent)' : 'var(--text-muted)' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleSessionPin(s.id)
                      refreshMeta()
                    }}
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
                    <span className="truncate flex-1 min-w-0 font-medium">
                      {s.title || 'New Session'}
                    </span>
                  )}
                  <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
                    {relativeTime(s.updated_at)}
                  </span>
                  {onRename && !isRenaming && (
                    <button
                      type="button"
                      className="opacity-0 group-hover:opacity-80 p-0.5"
                      style={{ color: 'var(--text-muted)' }}
                      onClick={(e) => {
                        e.stopPropagation()
                        onStartRename(s)
                      }}
                    >
                      <IconEdit size={12} />
                    </button>
                  )}
                  <button
                    className="w-5 h-5 opacity-0 group-hover:opacity-70"
                    style={{ color: 'var(--error)' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(s.id)
                    }}
                  >
                    ×
                  </button>
                </div>
                <div className="flex items-center gap-1 px-1 mt-0.5 min-h-[1rem] pl-5">
                  <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
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
              {isNone ? 'No unattached sessions' : 'Drop sessions here or use +'}
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
      className="text-[10px] px-1.5 py-0.5 rounded-full"
      style={{
        background: active ? 'var(--accent)' : 'var(--bg-tertiary)',
        color: active ? '#fff' : 'var(--text-muted)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
      }}
    >
      {label}
    </button>
  )
}
