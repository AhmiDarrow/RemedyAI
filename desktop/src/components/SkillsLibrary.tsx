import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  checkLibraryUpdates,
  fetchLibraryCatalog,
  installLibrarySkill,
  searchLibrary,
  type LibrarySkill,
} from '../api/skillsLibrary'
import { setSkillQuarantine, setSkillStatus, type SkillRow } from '../api/skills'

/** Min interval between automatic (silent) force-refreshes when switching tabs. */
const AUTO_FORCE_MIN_MS = 45_000

/**
 * Browse / install skills from the signed Skills Library catalog.
 * Installs land quarantined; Trust activates after install.
 *
 * Load strategy (avoids stutter):
 * 1. Show cached catalog first (server 24h cache / fast path) — full-screen
 *    loading only when the list is still empty.
 * 2. Background force-refresh updates the list in place (no blank flash).
 * 3. Manual Refresh always force-pulls; shows a subtle "Refreshing…" chip.
 * 4. Auto force on tab focus is throttled so rapid tab flips stay smooth.
 */
export function SkillsLibrary({
  onInstalled,
  installed = [],
  /** True when Library tab is visible — triggers soft background refresh. */
  active = true,
}: {
  onInstalled?: () => void
  installed?: SkillRow[]
  active?: boolean
}) {
  const [q, setQ] = useState('')
  const [skills, setSkills] = useState<LibrarySkill[]>([])
  const [source, setSource] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [updateIds, setUpdateIds] = useState<Set<string>>(new Set())

  const skillsLenRef = useRef(0)
  skillsLenRef.current = skills.length
  const genRef = useRef(0)
  const inFlightRef = useRef(false)
  const lastForceAtRef = useRef(0)
  const qRef = useRef(q)
  qRef.current = q

  const installedByName = useMemo(() => {
    const m = new Map<string, SkillRow>()
    for (const s of installed) m.set(s.name, s)
    return m
  }, [installed])

  const loadUpdates = useCallback(async () => {
    try {
      const u = await checkLibraryUpdates()
      setUpdateIds(new Set((u.updates || []).map((x) => x.skill_id || x.name)))
    } catch {
      setUpdateIds(new Set())
    }
  }, [])

  /**
   * @param force  Hit remote catalog (bypass 24h server cache).
   * @param silent Never blank the list (in-place update only).
   * @param reason auto = tab focus / post-mount (throttled); user = Refresh button.
   */
  const load = useCallback(
    async (opts?: {
      force?: boolean
      silent?: boolean
      reason?: 'auto' | 'user' | 'mount' | 'search'
    }) => {
      const force = Boolean(opts?.force)
      const silent = Boolean(opts?.silent)
      const reason = opts?.reason ?? 'user'
      const query = qRef.current.trim()

      // Auto soft-refresh only: coalesce + throttle (user Refresh always runs).
      if (reason === 'auto') {
        if (inFlightRef.current) return
        if (force && Date.now() - lastForceAtRef.current < AUTO_FORCE_MIN_MS) return
      }

      const myGen = ++genRef.current
      inFlightRef.current = true

      // Full-screen "Loading…" only on first empty paint — never wipe an existing list.
      if (!silent && skillsLenRef.current === 0) setLoading(true)
      if (force || silent) setRefreshing(true)
      if (reason === 'user' || reason === 'search') setError(null)

      try {
        if (query) {
          const r = await searchLibrary(query)
          if (myGen !== genRef.current) return
          setSkills(r.results || [])
          setSource(r.source || 'search')
        } else {
          const cat = await fetchLibraryCatalog(force)
          if (myGen !== genRef.current) return
          setSkills(cat.skills || [])
          setSource(cat.source || (force ? 'remote' : ''))
        }
        if (force) lastForceAtRef.current = Date.now()
        await loadUpdates()
      } catch (e) {
        if (myGen !== genRef.current) return
        // Keep previous skills on background failure; only hard-fail empty first load.
        if (skillsLenRef.current === 0) {
          setSkills([])
          setError(e instanceof Error ? e.message : 'Failed to load library')
        } else if (reason === 'user') {
          setError(e instanceof Error ? e.message : 'Refresh failed')
        }
      } finally {
        if (myGen === genRef.current) {
          setLoading(false)
          setRefreshing(false)
          inFlightRef.current = false
        }
      }
    },
    [loadUpdates],
  )

  // First mount: paint from cache, then soft remote refresh.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      await load({ force: false, silent: false, reason: 'mount' })
      if (cancelled) return
      // Soft upgrade to remote without blanking the list.
      await load({ force: true, silent: true, reason: 'auto' })
    })()
    return () => {
      cancelled = true
      genRef.current += 1
      inFlightRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional once-on-mount
  }, [])

  // User switched to Library tab: soft background refresh (throttled).
  const wasActive = useRef(active)
  useEffect(() => {
    const becameActive = active && !wasActive.current
    wasActive.current = active
    if (!becameActive) return
    void load({ force: true, silent: true, reason: 'auto' })
  }, [active, load])

  const install = async (id: string, forceUpdate = false) => {
    setBusy(id)
    setError(null)
    setMsg(null)
    try {
      const r = await installLibrarySkill(id, { force: forceUpdate })
      setMsg(r.message || `Installed ${r.names?.join(', ')}`)
      onInstalled?.()
      await load({ force: false, silent: true, reason: 'auto' })
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

  const onManualRefresh = () => {
    // Manual always bypasses auto-throttle; keep list visible if we already have rows.
    void load({
      force: true,
      silent: skillsLenRef.current > 0,
      reason: 'user',
    })
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="flex gap-1 items-center">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            // Don't let global/composer handlers treat Enter as "send".
            e.stopPropagation()
            if (e.key === 'Enter') {
              e.preventDefault()
              void load({ force: false, silent: false, reason: 'search' })
            }
          }}
          onMouseDown={(e) => {
            // Ensure click focuses the field even while stream UI is updating.
            e.stopPropagation()
          }}
          placeholder="Search library…"
          autoComplete="off"
          spellCheck={false}
          data-keep-focus
          aria-label="Search skills library"
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
          onClick={() => void load({ force: false, silent: false, reason: 'search' })}
        >
          Go
        </button>
        <button
          type="button"
          className="text-[10px] px-2 py-1.5 rounded min-w-[4.5rem]"
          style={{
            border: '1px solid var(--border)',
            color: refreshing ? 'var(--accent)' : 'var(--text-secondary)',
            opacity: refreshing ? 0.9 : 1,
          }}
          onClick={onManualRefresh}
          disabled={refreshing && skills.length === 0}
          title="Re-fetch signed catalog from the network"
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <p className="text-[10px] m-0 flex items-center gap-1.5 flex-wrap" style={{ color: 'var(--text-muted)' }}>
        <span>
          Install → review → Trust.
          {skills.length ? ` ${skills.length} shown` : ''}
          {source ? ` · ${source}` : ''}
        </span>
        {refreshing && skills.length > 0 && (
          <span style={{ color: 'var(--accent)' }} aria-live="polite">
            Updating catalog…
          </span>
        )}
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
      {loading && skills.length === 0 ? (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Loading library…
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
