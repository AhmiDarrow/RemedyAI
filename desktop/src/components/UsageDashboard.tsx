import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  exportUsageCsv,
  getContinuityDashboard,
  getUsageSeries,
  getUsageSummary,
  type ContinuityDashboard,
  type UsageSeriesPoint,
  type UsageSummary,
} from '../api/usage'
import { formatCost, formatTokens } from '../utils/tokenCost'
import { EmptyState } from './EmptyState'

interface UsageDashboardProps {
  open: boolean
  onClose: () => void
  sessionId?: string | null
  provider?: string
  model?: string
}

function BarChart({
  points,
  valueKey = 'total_tokens',
}: {
  points: UsageSeriesPoint[]
  valueKey?: 'total_tokens' | 'estimated_cost_usd'
}) {
  const byDay = useMemo(() => {
    const map = new Map<string, number>()
    for (const p of points) {
      map.set(p.day, (map.get(p.day) || 0) + Number(p[valueKey] || 0))
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [points, valueKey])

  const max = Math.max(1, ...byDay.map(([, v]) => v))
  if (byDay.length === 0) {
    return (
      <EmptyState
        compact
        title="No usage data yet"
        description="Send a few messages and costs will appear here."
      />
    )
  }

  return (
    <div className="flex items-end gap-1 h-28 px-1">
      {byDay.map(([day, val]) => (
        <div key={day} className="flex-1 flex flex-col items-center gap-1 min-w-0">
          <div
            className="w-full rounded-t"
            title={`${day}: ${valueKey === 'estimated_cost_usd' ? formatCost(val) : formatTokens(val)}`}
            style={{
              height: `${Math.max(4, Math.round((val / max) * 100))}%`,
              background: 'var(--accent)',
              opacity: 0.85,
            }}
          />
          <span className="text-[9px] truncate w-full text-center" style={{ color: 'var(--text-muted)' }}>
            {day.slice(5)}
          </span>
        </div>
      ))}
    </div>
  )
}

export function UsageDashboard({
  open,
  onClose,
  sessionId,
  provider,
  model,
}: UsageDashboardProps) {
  const [range, setRange] = useState(7)
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [series, setSeries] = useState<UsageSeriesPoint[]>([])
  const [continuity, setContinuity] = useState<ContinuityDashboard | null>(null)
  const [tab, setTab] = useState<'usage' | 'continuity'>('usage')
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!open) return
    setErr(null)
    try {
      const [s, ser, c] = await Promise.all([
        getUsageSummary(range),
        getUsageSeries(Math.max(range, 14), 'provider'),
        getContinuityDashboard(sessionId),
      ])
      setSummary(s)
      setSeries(ser.points || [])
      setContinuity(c)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [open, range, sessionId])

  useEffect(() => {
    load()
  }, [load])

  if (!open) return null

  const q = continuity?.session_quality as Record<string, number | string | null> | undefined
  const fill =
    (continuity?.token?.last_remeasure as { fill_pct?: number } | null | undefined)?.fill_pct
    ?? (continuity?.context_snapshot as { fill_pct?: number } | null | undefined)?.fill_pct

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 ui-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="ui-surface w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col"
        style={{ color: 'var(--text-primary)', background: 'var(--bg-primary)' }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Usage and continuity dashboard"
      >
        <div
          className="flex items-center justify-between px-4 py-3 border-b"
          style={{ borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)' }}
        >
          <div>
            <div className="text-sm font-semibold tracking-tight">Usage & Continuity</div>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              NanoToken multiprovider accounting · harness quality
              {provider ? ` · active ${provider}/${model || '…'}` : ''}
            </div>
          </div>
          <button type="button" className="ui-btn ui-btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="flex gap-1 px-4 pt-2">
          {(['usage', 'continuity'] as const).map((t) => (
            <button
              key={t}
              type="button"
              className={`seg-btn capitalize${tab === t ? ' is-active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t === 'usage' ? 'Usage & cost' : 'Harness'}
            </button>
          ))}
          {tab === 'usage' && (
            <>
              <select
                className="ml-auto text-xs rounded px-2 py-1"
                value={range}
                onChange={(e) => setRange(Number(e.target.value))}
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                }}
              >
                <option value={1}>1 day</option>
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
              </select>
              <button
                type="button"
                className="text-xs px-2 py-1 rounded"
                style={{ border: '1px solid var(--border)' }}
                onClick={() => {
                  void exportUsageCsv(range)
                    .then((csv) => {
                      const blob = new Blob([csv], { type: 'text/csv' })
                      const url = URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = `remedy-usage-${range}d.csv`
                      a.click()
                      URL.revokeObjectURL(url)
                    })
                    .catch((e) => setErr(e instanceof Error ? e.message : 'Export failed'))
                }}
              >
                Export CSV
              </button>
            </>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {err && (
            <div className="text-xs rounded px-3 py-2" style={{ background: 'var(--error)', color: '#fff' }}>
              {err}
            </div>
          )}

          {tab === 'usage' && summary && (
            <>
              <div className="grid grid-cols-3 gap-2">
                {[
                  ['Tokens', formatTokens(summary.totals.total_tokens)],
                  ['Est. cost', formatCost(summary.totals.estimated_cost_usd)],
                  ['Events', String(summary.totals.events)],
                ].map(([label, val]) => (
                  <div
                    key={label}
                    className="rounded px-3 py-2"
                    style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                  >
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{label}</div>
                    <div className="text-sm font-semibold">{val}</div>
                  </div>
                ))}
              </div>

              <div>
                <div className="text-xs font-medium mb-1">Tokens over time</div>
                <div
                  className="rounded p-2"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <BarChart points={series} />
                </div>
              </div>

              <div>
                <div className="text-xs font-medium mb-1">By provider</div>
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ color: 'var(--text-muted)' }}>
                      <th className="text-left py-1">Provider</th>
                      <th className="text-right">Tokens</th>
                      <th className="text-right">Cost</th>
                      <th className="text-right">Events</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(summary.by_provider || []).map((row) => (
                      <tr key={row.provider} style={{ borderTop: '1px solid var(--border)' }}>
                        <td className="py-1.5">{row.provider || 'unknown'}</td>
                        <td className="text-right">{formatTokens(row.total_tokens)}</td>
                        <td className="text-right">{formatCost(row.estimated_cost_usd)}</td>
                        <td className="text-right">{row.events}</td>
                      </tr>
                    ))}
                    {(summary.by_provider || []).length === 0 && (
                      <tr>
                        <td colSpan={4} className="py-3 text-center" style={{ color: 'var(--text-muted)' }}>
                          No provider breakdown yet
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {(summary.by_model || []).length > 0 && (
                <div>
                  <div className="text-xs font-medium mb-1">By model</div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ color: 'var(--text-muted)' }}>
                        <th className="text-left py-1">Model</th>
                        <th className="text-right">Tokens</th>
                        <th className="text-right">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.by_model.slice(0, 12).map((row) => (
                        <tr
                          key={`${row.provider}-${row.model}`}
                          style={{ borderTop: '1px solid var(--border)' }}
                        >
                          <td className="py-1.5">
                            <span style={{ color: 'var(--text-muted)' }}>{row.provider}/</span>
                            {row.model}
                          </td>
                          <td className="text-right">{formatTokens(row.total_tokens)}</td>
                          <td className="text-right">{formatCost(row.estimated_cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {tab === 'continuity' && continuity && (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div
                  className="rounded px-3 py-2"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Context fill</div>
                  <div className="text-lg font-semibold">
                    {fill != null ? `${Math.round(Number(fill) * 100)}%` : '—'}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    method {String(continuity.token.last_method || '—')} · estimate{' '}
                    {formatTokens(Number(continuity.token.last_estimate || 0))}
                  </div>
                </div>
                <div
                  className="rounded px-3 py-2"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Harness</div>
                  <div className="text-lg font-semibold capitalize">{continuity.harness_mode}</div>
                  <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    turns {String(q?.turns ?? '—')} · saved{' '}
                    {formatTokens(Number(q?.tokens_saved_by_compress || 0))}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 text-xs">
                {[
                  ['Soft nudges', q?.soft_nudge_count],
                  ['Strong nudges', q?.strong_nudge_count],
                  ['Compresses', q?.compress_count],
                  ['Stuck rate', q?.stuck_rate != null ? `${(Number(q.stuck_rate) * 100).toFixed(1)}%` : '—'],
                  ['Re-explain', q?.re_explain_rate != null ? `${(Number(q.re_explain_rate) * 100).toFixed(1)}%` : '—'],
                  ['Tool fail max', q?.max_tool_fail_streak],
                ].map(([label, val]) => (
                  <div
                    key={String(label)}
                    className="rounded px-2 py-2"
                    style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                  >
                    <div style={{ color: 'var(--text-muted)' }}>{label}</div>
                    <div className="font-medium">{val == null ? '—' : String(val)}</div>
                  </div>
                ))}
              </div>

              <div
                className="rounded px-3 py-2 text-xs"
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
              >
                <div className="font-medium mb-1">Pattern window (this session)</div>
                <div style={{ color: 'var(--text-muted)' }}>
                  {continuity.pattern.step_count} steps
                  {continuity.pattern.success_rate != null
                    ? ` · ${(continuity.pattern.success_rate * 100).toFixed(0)}% success`
                    : ''}
                </div>
                {continuity.pattern.recent?.length > 0 && (
                  <div className="mt-1 font-mono text-[10px]">
                    {continuity.pattern.recent.join(' → ')}
                  </div>
                )}
              </div>

              {continuity.context_snapshot && (
                <div
                  className="rounded px-3 py-2 text-xs"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <div className="font-medium mb-1">Last continuity snapshot</div>
                  <div style={{ color: 'var(--text-muted)' }}>
                    intent {(continuity.context_snapshot as { intent?: string }).intent || '—'} · policy{' '}
                    {(continuity.context_snapshot as { policy_id?: string }).policy_id || '—'}
                    {(continuity.context_snapshot as { nudge?: string }).nudge
                      ? ` · nudge ${(continuity.context_snapshot as { nudge?: string }).nudge}`
                      : ''}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div
                  className="rounded px-3 py-2"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <div className="font-medium mb-1">Goals</div>
                  <div style={{ color: 'var(--text-muted)' }}>
                    {(continuity.goal?.open || []).length
                      ? (continuity.goal?.open || []).slice(0, 4).join(' · ')
                      : 'No open goals tracked'}
                    {continuity.goal?.stale ? ' · stale' : ''}
                  </div>
                </div>
                <div
                  className="rounded px-3 py-2"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                >
                  <div className="font-medium mb-1">Provider health</div>
                  <div style={{ color: 'var(--text-muted)' }}>
                    samples {continuity.health?.samples ?? 0}
                    {continuity.health?.error_rate != null
                      ? ` · err ${(Number(continuity.health.error_rate) * 100).toFixed(0)}%`
                      : ''}
                    {continuity.health?.avg_latency_ms != null
                      ? ` · ~${Math.round(Number(continuity.health.avg_latency_ms))}ms`
                      : ''}
                    {continuity.health?.flaky ? ' · flaky' : ''}
                    {continuity.health?.rate_limit_hits
                      ? ` · 429×${continuity.health.rate_limit_hits}`
                      : ''}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
