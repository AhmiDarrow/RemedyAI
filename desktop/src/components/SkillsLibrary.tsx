import { useCallback, useEffect, useState } from 'react'
import {
  fetchLibraryCatalog,
  installLibrarySkill,
  searchLibrary,
  type LibrarySkill,
} from '../api/skillsLibrary'
import { setSkillQuarantine, setSkillStatus } from '../api/skills'

/**
 * Browse / install skills from the signed Skills Library catalog.
 * Installs land quarantined; Trust uses existing skill APIs.
 */
export function SkillsLibrary({ onInstalled }: { onInstalled?: () => void }) {
  const [q, setQ] = useState('')
  const [skills, setSkills] = useState<LibrarySkill[]>([])
  const [source, setSource] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const load = useCallback(async (refresh = false) => {
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
    } catch (e) {
      setSkills([])
      setError(e instanceof Error ? e.message : 'Failed to load library')
    } finally {
      setLoading(false)
    }
  }, [q])

  useEffect(() => {
    void load(false)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const install = async (id: string) => {
    setBusy(id)
    setError(null)
    setMsg(null)
    try {
      const r = await installLibrarySkill(id)
      setMsg(r.message || `Installed ${r.names?.join(', ')} (quarantined)`)
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
      setMsg(`Trusted & activated: ${name}`)
      onInstalled?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trust failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <p className="text-[10px] m-0" style={{ color: 'var(--text-muted)' }}>
        Community skills from the signed catalog. Installs stay{' '}
        <strong style={{ color: 'var(--text-secondary)' }}>quarantined</strong> until you Trust.
        {source ? ` · source: ${source}` : ''}
      </p>
      <div className="flex gap-1">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void load()}
          placeholder="Search library…"
          className="flex-1 text-xs px-2 py-1 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="button"
          className="text-[10px] px-2 py-1 rounded"
          style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          onClick={() => void load()}
        >
          Search
        </button>
        <button
          type="button"
          className="text-[10px] px-2 py-1 rounded"
          style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          onClick={() => void load(true)}
          title="Force refresh catalog"
        >
          Refresh
        </button>
      </div>
      {error && (
        <div className="text-[10px] px-2 py-1 rounded" style={{ color: 'var(--error)' }}>
          {error}
        </div>
      )}
      {msg && (
        <div className="text-[10px] px-2 py-1 rounded" style={{ color: 'var(--accent)' }}>
          {msg}
        </div>
      )}
      {loading ? (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Loading catalog…
        </div>
      ) : skills.length === 0 ? (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          No skills in catalog. Ensure community/remedy-skills is signed locally, or the remote
          catalog is published.
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-2 min-h-0">
          {skills.map((s) => (
            <div
              key={s.id}
              className="p-2 rounded"
              style={{
                background: 'var(--bg-tertiary)',
                border: '1px solid var(--border)',
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                    {s.name}{' '}
                    <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                      v{s.version}
                    </span>
                  </div>
                  <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.description}
                  </div>
                  <div className="text-[9px] mt-1" style={{ color: 'var(--text-muted)' }}>
                    {s.author}
                    {s.tags?.length ? ` · ${s.tags.join(', ')}` : ''}
                    {s.security_flags?.length
                      ? ` · flags: ${s.security_flags.join(', ')}`
                      : ''}
                  </div>
                </div>
              </div>
              <div className="flex gap-1 mt-2">
                <button
                  type="button"
                  disabled={busy === s.id}
                  className="text-[10px] px-2 py-1 rounded"
                  style={{
                    background: 'var(--accent)',
                    color: 'var(--bg-primary)',
                    border: 'none',
                    opacity: busy === s.id ? 0.6 : 1,
                  }}
                  onClick={() => void install(s.id)}
                >
                  {busy === s.id ? '…' : 'Install'}
                </button>
                <button
                  type="button"
                  disabled={busy === s.name || busy === s.id}
                  className="text-[10px] px-2 py-1 rounded"
                  style={{
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                  title="Clear quarantine and activate (after install)"
                  onClick={() => void trust(s.name)}
                >
                  Trust
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
