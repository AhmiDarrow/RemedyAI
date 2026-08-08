import { useCallback, useState } from 'react'
import {
  dismissLibrarySuggest,
  installLibrarySkill,
  type LibrarySuggest,
} from '../api/skillsLibrary'
import { setSkillQuarantine, setSkillStatus } from '../api/skills'

/**
 * Soft tip: library pack may help.
 * Install (primary) downloads + Trusts so the pack is usable without opening Skills.
 * Open Library still available for browse; never auto-installs without a click.
 */
export function LibrarySuggestChip({
  suggestion,
  sessionId,
  onOpenLibrary,
  onDismiss,
  onInstalled,
}: {
  suggestion: LibrarySuggest | null
  sessionId?: string | null
  onOpenLibrary?: (skillId: string, name: string) => void
  onDismiss?: () => void
  /** Called after successful install (+ trust) so Installed list can refresh. */
  onInstalled?: (names: string[]) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [doneMsg, setDoneMsg] = useState<string | null>(null)

  const dismiss = useCallback(async () => {
    if (!suggestion?.id) {
      onDismiss?.()
      return
    }
    try {
      await dismissLibrarySuggest(suggestion.id, sessionId || undefined)
    } catch {
      /* still clear UI */
    }
    onDismiss?.()
  }, [suggestion, sessionId, onDismiss])

  const install = useCallback(async () => {
    if (!suggestion?.id || busy) return
    setBusy(true)
    setError(null)
    setDoneMsg(null)
    try {
      const r = await installLibrarySkill(suggestion.id, {
        version: suggestion.version || undefined,
      })
      const names = Array.isArray(r.names) && r.names.length ? r.names : [suggestion.name]
      // Trust so activate/run works without a second trip to Skills panel
      for (const name of names) {
        try {
          await setSkillQuarantine(name, false)
          await setSkillStatus(name, 'active', {
            force_promote: true,
            quarantine: false,
          })
        } catch {
          /* install succeeded; trust may need manual step */
        }
      }
      setDoneMsg(`Installed & ready: ${names.join(', ')}`)
      onInstalled?.(names)
      try {
        await dismissLibrarySuggest(suggestion.id, sessionId || undefined)
      } catch {
        /* */
      }
      // Brief success flash then clear chip
      window.setTimeout(() => {
        onDismiss?.()
      }, 1600)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed')
    } finally {
      setBusy(false)
    }
  }, [suggestion, busy, sessionId, onInstalled, onDismiss])

  if (!suggestion?.id || !suggestion.name) return null

  const desc = (suggestion.description || '').trim()
  const short = desc.length > 90 ? desc.slice(0, 87) + '…' : desc

  return (
    <div
      className="ui-banner flex flex-col gap-1.5 mx-3 mb-1.5 text-xs"
      style={{
        background: 'color-mix(in srgb, var(--accent) 8%, var(--bg-secondary))',
        borderColor: 'color-mix(in srgb, var(--accent) 28%, var(--border))',
        color: 'var(--text-primary)',
        padding: '0.55rem 0.7rem',
      }}
      role="status"
    >
      <div className="flex items-center gap-2">
        <span className="flex-1 min-w-0 leading-snug" style={{ color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--accent)' }}>Library</strong>
          {' · '}
          <span className="font-medium" style={{ color: 'var(--text-primary)' }}>
            {suggestion.name}
          </span>
          {short ? <span style={{ opacity: 0.85 }}> — {short}</span> : null}
        </span>
        <button
          type="button"
          className="ui-btn ui-btn-primary shrink-0"
          style={{ padding: '0.25rem 0.55rem', fontSize: '0.68rem' }}
          disabled={busy || Boolean(doneMsg)}
          onClick={() => void install()}
          title="Download pack and Trust so Remedy can use it now"
        >
          {busy ? 'Installing…' : doneMsg ? 'Done' : 'Install'}
        </button>
        <button
          type="button"
          className="ui-btn ui-btn-secondary shrink-0"
          style={{ padding: '0.25rem 0.55rem', fontSize: '0.68rem' }}
          disabled={busy}
          onClick={() => onOpenLibrary?.(suggestion.id, suggestion.name)}
          title="Browse in Skills → Library"
        >
          Library
        </button>
        <button
          type="button"
          className="ui-btn ui-btn-ghost shrink-0"
          style={{ padding: '0.2rem 0.4rem', fontSize: '0.75rem' }}
          aria-label="Dismiss library suggestion"
          disabled={busy}
          onClick={() => void dismiss()}
        >
          ×
        </button>
      </div>
      {error ? (
        <div style={{ color: 'var(--error)' }}>{error}</div>
      ) : null}
      {doneMsg ? (
        <div style={{ color: 'var(--success)' }}>{doneMsg}</div>
      ) : null}
    </div>
  )
}
