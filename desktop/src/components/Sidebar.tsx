import { useMemo, useState, useEffect, useRef } from 'react'
import type { ChatSession } from '../types'
import { relativeTime } from '../utils/relativeTime'
import {
  getAllSessionMeta,
  setSessionMeta,
  toggleSessionPin,
  type SessionMeta,
} from '../utils/sessionMeta'
import { IconEdit } from './icons'

interface SidebarProps {
  sessions: ChatSession[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRename?: (id: string, title: string) => void
}

export function Sidebar({
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [meta, setMeta] = useState<Record<string, SessionMeta>>(() => getAllSessionMeta())
  const [tagDraft, setTagDraft] = useState('')
  const [tagTarget, setTagTarget] = useState<string | null>(null)
  const [folderFilter, setFolderFilter] = useState<string | 'all' | 'pinned'>('all')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const renameRef = useRef<HTMLInputElement>(null)
  const [, setTick] = useState(0)

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

  const folders = useMemo(() => {
    const set = new Set<string>()
    for (const m of Object.values(meta)) {
      if (m.folder) set.add(m.folder)
    }
    return [...set].sort((a, b) => a.localeCompare(b))
  }, [meta])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = [...sessions]
    list.sort((a, b) => {
      const ap = meta[a.id]?.pinned ? 1 : 0
      const bp = meta[b.id]?.pinned ? 1 : 0
      if (ap !== bp) return bp - ap
      return (b.updated_at || '').localeCompare(a.updated_at || '')
    })
    if (folderFilter === 'pinned') {
      list = list.filter((s) => meta[s.id]?.pinned)
    } else if (folderFilter !== 'all') {
      list = list.filter((s) => (meta[s.id]?.folder || '') === folderFilter)
    }
    if (!q) return list
    return list.filter((s) => {
      const title = (s.title || '').toLowerCase()
      const tags = (meta[s.id]?.tags || []).join(' ').toLowerCase()
      const folder = (meta[s.id]?.folder || '').toLowerCase()
      return title.includes(q) || tags.includes(q) || folder.includes(q)
    })
  }, [sessions, query, meta, folderFilter])

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

  return (
    <div
      className="flex flex-col border-r"
      style={{
        width: 240,
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
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search sessions, tags…"
          className="w-full rounded-md px-2.5 py-1.5 text-xs outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          aria-label="Search sessions"
        />
        <div className="flex flex-wrap gap-1">
          <FilterChip
            active={folderFilter === 'all'}
            onClick={() => setFolderFilter('all')}
            label="All"
          />
          <FilterChip
            active={folderFilter === 'pinned'}
            onClick={() => setFolderFilter('pinned')}
            label="★ Pin"
          />
          {folders.map((f) => (
            <FilterChip
              key={f}
              active={folderFilter === f}
              onClick={() => setFolderFilter(f)}
              label={f}
            />
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {filtered.map((s) => {
          const m = meta[s.id] || {}
          const pinned = Boolean(m.pinned)
          const isRenaming = renamingId === s.id
          return (
            <div
              key={s.id}
              className="group flex flex-col px-2 cursor-pointer text-sm transition-colors relative"
              style={{
                background: s.id === activeId ? 'var(--bg-tertiary)' : 'transparent',
                color: s.id === activeId ? 'var(--text-primary)' : 'var(--text-secondary)',
                borderLeft: s.id === activeId ? '3px solid var(--accent)' : '3px solid transparent',
                paddingTop: 'var(--sidebar-row-py)',
                paddingBottom: 'var(--sidebar-row-py)',
              }}
              onClick={() => {
                if (!isRenaming) onSelect(s.id)
              }}
              onDoubleClick={(e) => {
                e.stopPropagation()
                startRename(s)
              }}
              onMouseEnter={(e) => {
                if (s.id !== activeId) e.currentTarget.style.background = 'var(--bg-tertiary)'
              }}
              onMouseLeave={(e) => {
                if (s.id !== activeId) e.currentTarget.style.background = 'transparent'
              }}
            >
              <div className="flex items-center gap-1.5 px-1">
                <button
                  type="button"
                  className="flex-shrink-0 text-xs w-4 opacity-50 group-hover:opacity-100"
                  title={pinned ? 'Unpin' : 'Pin'}
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
                    onBlur={() => commitRename(s.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        commitRename(s.id)
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
                    aria-label="Rename session"
                  />
                ) : (
                  <span
                    className="truncate flex-1 min-w-0 font-medium"
                    title={`${s.title || 'New Session'} — double-click to rename`}
                  >
                    {s.title || 'New Session'}
                  </span>
                )}
                <span
                  className="text-[10px] flex-shrink-0"
                  style={{ color: 'var(--text-muted)' }}
                  title={s.updated_at}
                >
                  {relativeTime(s.updated_at)}
                </span>
                {onRename && !isRenaming && (
                  <button
                    type="button"
                    className="flex-shrink-0 opacity-0 group-hover:opacity-80 p-0.5 rounded"
                    style={{ color: 'var(--text-muted)' }}
                    title="Rename"
                    aria-label="Rename"
                    onClick={(e) => {
                      e.stopPropagation()
                      startRename(s)
                    }}
                  >
                    <IconEdit size={12} />
                  </button>
                )}
                <button
                  className="flex-shrink-0 w-5 h-5 text-sm leading-none rounded opacity-0 pointer-events-none group-hover:opacity-70 group-hover:pointer-events-auto hover:!opacity-100"
                  style={{ color: 'var(--error)' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(s.id)
                  }}
                  title="Delete"
                  aria-label="Delete"
                >
                  ×
                </button>
              </div>
              <div className="flex items-center gap-1 px-1 mt-0.5 min-h-[1rem]">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {s.message_count} msg
                </span>
                {m.folder && (
                  <span
                    className="text-[10px] px-1 rounded"
                    style={{ background: 'var(--bg-primary)', color: 'var(--text-muted)' }}
                  >
                    {m.folder}
                  </span>
                )}
                {(m.tags || []).slice(0, 3).map((t) => (
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
                  title="Add tag / folder"
                  onClick={(e) => {
                    e.stopPropagation()
                    setTagTarget(tagTarget === s.id ? null : s.id)
                    setTagDraft('')
                  }}
                >
                  +tag
                </button>
              </div>
              {tagTarget === s.id && (
                <div
                  className="px-1 mt-1 flex gap-1"
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    value={tagDraft}
                    onChange={(e) => setTagDraft(e.target.value)}
                    placeholder="tag or folder:name"
                    className="flex-1 rounded px-1.5 py-0.5 text-[10px] outline-none"
                    style={{
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border)',
                      color: 'var(--text-primary)',
                    }}
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && tagDraft.trim()) {
                        const raw = tagDraft.trim()
                        if (raw.toLowerCase().startsWith('folder:')) {
                          setSessionMeta(s.id, { folder: raw.slice(7).trim() })
                        } else {
                          const tags = [...(getAllSessionMeta()[s.id]?.tags || []), raw]
                          setSessionMeta(s.id, { tags })
                        }
                        refreshMeta()
                        setTagDraft('')
                        setTagTarget(null)
                      }
                      if (e.key === 'Escape') setTagTarget(null)
                    }}
                  />
                </div>
              )}
            </div>
          )
        })}

        {filtered.length === 0 && (
          <div className="px-3 py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
            {sessions.length === 0 ? 'No sessions yet' : 'No matches'}
          </div>
        )}
      </div>
      <div
        className="px-3 py-1.5 text-[10px] border-t"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        Double-click a session to rename
      </div>
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
