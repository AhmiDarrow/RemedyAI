import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  checkLibraryUpdates,
  fetchLibraryCatalog,
  installLibrarySkill,
  searchLibrary,
  type LibrarySkill,
} from '../api/skillsLibrary'
import { setSkillQuarantine, setSkillStatus, type SkillRow } from '../api/skills'

/**
 * Browse / install skills from the signed Skills Library catalog.
 * Installs land quarantined; Trust activates after install.
 */
export function SkillsLibrary({
  onInstalled,
  installed = [],
}: {
  onInstalled?: () => void
  installed?: SkillRow[]
}) {
  const [q, setQ] = useState('')
  const [skills, setSkills] = useState<LibrarySkill[]>([])
  const [source, setSource] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [updateIds, setUpdateIds] = useState<Set<string>>(new Set())

  const installedByName = useMemo(() => {
    const m = new Map<string, SkillRow>()
    for (const s of installed) m.set(s.name, s)
    return m
  }, [installed])

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true)
      setError(null)
      try {
        if (q.trim()) {
          const r = await searchLibrary(q)
          setSkills(r.results || [])
          setSource(r.source || '')
        } else {
          const cat = await fetchLibraryCatalog(refresh)
          setSkills(cat.skills || [])
          setSource(cat.source || '')
        }
        try {
          const u = await checkLibraryUpdates()
          setUpdateIds(new Set((u.updates || []).map((x) => x.skill_id || x.name)))
        } catch {
          setUpdateIds(new Set())
        }
      } catch (e) {
        setSkills([])
        setError(e instanceof Error ? e.message : 'Failed to load library')
      } finally {
        setLoading(false)
      }
    },
    [q],
  )

  useEffect(() => {
    void load(false)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const install = async (id: string, force = false) => {
    setBusy(id)
    setError(null)
    setMsg(null)
    try {
      const r = await installLibrarySkill(id, { force })
      setMsg(r.message || `Installed ${r.names?.join(', ')}`)
      onInstalled?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed')
    } finally {
      setBusy(null)
    }
  }

  const trust = async (name: string) => {
    setBusy(name)
    setError(null)
    try {
      await setSkillQuarantine(name, false)
      await setSkillStatus(name, 'active', { force_promote: true, quarantine: false })
      setMsg(`Trusted: ${name}`)
      onInstalled?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trust failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="flex gap-1 items-center">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void load()}
          placeholder="Search library…"
          className="flex-1 text-xs px-2 py-1.5 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="button"
          className="text-[10px] px-2 py-1.5 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          onClick={() => void load()}
        >
          Go
        </button>
        <button
          type="button"
          className="text-[10px] px-2 py-1.5 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          onClick={() => void load(true)}
          title="Refresh signed catalog"
        >
          ↻
        </button>
      </div>
      <p className="text-[10px] m-0" style={{ color: 'var(--text-muted)' }}>
        Install → review → Trust. {skills.length ? `${skills.length} shown` : ''}
        {source ? ` · ${source}` : ''}
      </p>
      {error && (
        <div className="text-[10px]" style={{ color: 'var(--danger, #f66)' }}>
          {error}
        </div>
      )}
      {msg && (
        <div className="text-[10px]" style={{ color: 'var(--accent)' }}>
          {msg}
        </div>
      )}
      {loading ? (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Loading…
        </div>
      ) : skills.length === 0 ? (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          No matches in the catalog.
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-1.5 min-h-0">
          {skills.map((s) => {
            const local = installedByName.get(s.name)
            const isInstalled = Boolean(local)
            const needsTrust = Boolean(local?.quarantine)
            const hasUpdate = updateIds.has(s.id) || updateIds.has(s.name)
            const desc =
              s.description.length > 120 ? `${s.description.slice(0, 117)}…` : s.description
            return (
              <div
                key={s.id}
                className="px-2 py-1.5 rounded"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span
                        className="font-medium text-xs truncate"
                        style={{ color: 'var(--text-primary)' }}
                      >
                        {s.name}
                      </span>
                      <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
                        v{s.version}
                      </span>
                      {isInstalled && (
                        <span className="text-[9px]" style={{ color: 'var(--accent)' }}>
                          {needsTrust ? 'quarantined' : 'installed'}
                        </span>
                      )}
                      {hasUpdate && (
                        <span className="text-[9px]" style={{ color: 'var(--warning, #e6a23c)' }}>
                          update
                        </span>
                      )}
                    </div>
                    <div
                      className="text-[10px] mt-0.5 line-clamp-2"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {desc}
                    </div>
                  </div>
                  <div className="flex flex-col gap-1 shrink-0">
                    {!isInstalled ? (
                      <button
                        type="button"
                        disabled={busy === s.id}
                        className="text-[10px] px-2 py-0.5 rounded"
                        style={{
                          background: 'var(--accent)',
                          color: '#fff',
                          border: 'none',
                          opacity: busy === s.id ? 0.6 : 1,
                        }}
                        onClick={() => void install(s.id)}
                      >
                        {busy === s.id ? '…' : 'Install'}
                      </button>
                    ) : hasUpdate ? (
                      <button
                        type="button"
                        disabled={busy === s.id}
                        className="text-[10px] px-2 py-0.5 rounded"
                        style={{
                          border: '1px solid var(--border)',
                          color: 'var(--text-primary)',
                        }}
                        onClick={() => void install(s.id, true)}
                      >
                        Update
                      </button>
                    ) : null}
                    {needsTrust && (
                      <button
                        type="button"
                        disabled={busy === s.name || busy === s.id}
                        className="text-[10px] px-2 py-0.5 rounded"
                        style={{
                          border: '1px solid var(--accent)',
                          color: 'var(--accent)',
                        }}
                        title="Activate after review"
                        onClick={() => void trust(s.name)}
                      >
                        Trust
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
