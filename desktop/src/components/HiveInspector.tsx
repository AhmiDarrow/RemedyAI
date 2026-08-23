/** Advanced hive roster — daughters report to Remedy, never the owner. */

import { useCallback, useEffect, useState } from 'react'
import {
  getHiveRoster,
  hiveIsLive,
  hiveRowLabel,
  retireHiveDaughter,
  type HiveRoster,
  type HiveRosterRow,
} from '../api/hive'

export function HiveInspector({ open }: { open: boolean }) {
  const [roster, setRoster] = useState<HiveRoster | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!open) return
    try {
      const next = await getHiveRoster()
      setRoster(next)
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    void load()
    const id = window.setInterval(() => {
      void load()
    }, 8000)
    return () => window.clearInterval(id)
  }, [open, load])

  const onRetire = async (row: HiveRosterRow) => {
    if (pending !== row.id) {
      setPending(row.id)
      return
    }
    setBusy(true)
    try {
      const res = await retireHiveDaughter(row.id)
      if (!res.ok) setErr(res.error || 'retire failed')
      setPending(null)
      await load()
    } finally {
      setBusy(false)
    }
  }

  const rows = roster?.daughters || []
  const live = rows.filter(hiveIsLive)

  return (
    <section
      className="rounded-lg border px-3 py-2.5"
      style={{
        borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
        background: 'var(--bg-secondary, transparent)',
      }}
    >
      <div className="flex items-baseline justify-between gap-2 mb-1.5">
        <div>
          <div className="text-xs font-semibold tracking-tight">Hive</div>
          <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Daughters Remedy hired. They report to her, not you.
            {roster
              ? ` · ${live.length} live · ${roster.live_posts} posts · ${roster.live_foragers} foraging`
              : ''}
          </div>
        </div>
      </div>
      {err ? (
        <div className="text-[11px] mb-1" style={{ color: 'var(--error, #ef4444)' }}>
          {err}
        </div>
      ) : null}
      {rows.length === 0 ? (
        <div className="text-[11px] py-2" style={{ color: 'var(--text-muted)' }}>
          No daughters hired.
        </div>
      ) : (
        <ul className="space-y-1 max-h-48 overflow-y-auto">
          {rows.map((row) => (
            <li
              key={row.id}
              className="flex items-start justify-between gap-2 text-[11px] py-1"
              style={{
                borderBottom: '1px solid color-mix(in srgb, var(--border) 50%, transparent)',
              }}
            >
              <div className="min-w-0">
                <div className="font-medium truncate" title={row.goal}>
                  {hiveRowLabel(row)}
                </div>
                {row.outcome ? (
                  <div className="truncate" style={{ color: 'var(--text-muted)' }} title={row.outcome}>
                    {row.outcome}
                  </div>
                ) : null}
                {row.blockers?.length ? (
                  <div style={{ color: 'var(--warning, #f59e0b)' }}>
                    {row.blockers.slice(0, 2).join('; ')}
                  </div>
                ) : null}
              </div>
              {hiveIsLive(row) ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-ghost text-[10px] flex-shrink-0"
                  disabled={busy}
                  onClick={() => void onRetire(row)}
                >
                  {pending === row.id ? 'Confirm retire' : 'Retire'}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
