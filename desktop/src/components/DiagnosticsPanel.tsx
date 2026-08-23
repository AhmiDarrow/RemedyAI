/** Health Diagnostics — Remedy server, RMB, hardware, cloud providers. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getDiagnostics,
  type DiagnosticsIssue,
  type DiagnosticsOverall,
  type DiagnosticsSnapshot,
} from '../api/diagnostics'
import { formatCost, formatTokens } from '../utils/tokenCost'
import { browserStackHold } from '../utils/browserStack'
import { HiveInspector } from './HiveInspector'

interface DiagnosticsPanelProps {
  open: boolean
  onClose: () => void
}

function overallColor(overall: DiagnosticsOverall | undefined): string {
  if (overall === 'ok') return 'var(--success, #22c55e)'
  if (overall === 'degraded') return 'var(--warning, #f59e0b)'
  if (overall === 'error') return 'var(--error, #ef4444)'
  return 'var(--text-muted)'
}

function overallLabel(overall: DiagnosticsOverall | undefined): string {
  if (overall === 'ok') return 'Healthy'
  if (overall === 'degraded') return 'Degraded'
  if (overall === 'error') return 'Issues'
  return 'Unknown'
}

function Badge({
  ok,
  label,
  warn,
}: {
  ok?: boolean | null
  label: string
  warn?: boolean
}) {
  const color =
    ok === true
      ? 'var(--success, #22c55e)'
      : warn
        ? 'var(--warning, #f59e0b)'
        : ok === false
          ? 'var(--error, #ef4444)'
          : 'var(--text-muted)'
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
      style={{
        color,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
      }}
    >
      <span
        className="inline-block w-1.5 h-1.5 rounded-full"
        style={{ background: color }}
        aria-hidden
      />
      {label}
    </span>
  )
}

function Card({
  title,
  subtitle,
  badge,
  children,
}: {
  title: string
  subtitle?: string
  badge?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section
      className="rounded-xl p-3 space-y-2"
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-semibold tracking-tight">{title}</div>
          {subtitle ? (
            <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              {subtitle}
            </div>
          ) : null}
        </div>
        {badge}
      </div>
      {children}
    </section>
  )
}

function KV({
  rows,
}: {
  rows: Array<[string, string | number | null | undefined]>
}) {
  return (
    <dl className="grid grid-cols-[minmax(0,38%)_1fr] gap-x-2 gap-y-1 text-[11px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt style={{ color: 'var(--text-muted)' }}>{k}</dt>
          <dd
            className="font-medium truncate tabular-nums"
            title={v == null || v === '' ? undefined : String(v)}
            style={{ color: 'var(--text-primary)' }}
          >
            {v == null || v === '' ? '—' : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Meter({
  pct,
  label,
}: {
  pct: number | null | undefined
  label?: string
}) {
  if (pct == null || Number.isNaN(Number(pct))) return null
  const p = Math.max(0, Math.min(100, Number(pct)))
  const hot = p >= 90
  const warm = p >= 75
  const color = hot
    ? 'var(--error, #ef4444)'
    : warm
      ? 'var(--warning, #f59e0b)'
      : 'var(--accent)'
  return (
    <div className="space-y-0.5">
      {label ? (
        <div className="flex justify-between text-[10px]" style={{ color: 'var(--text-muted)' }}>
          <span>{label}</span>
          <span className="tabular-nums">{p.toFixed(0)}%</span>
        </div>
      ) : null}
      <div
        className="h-1.5 rounded-full overflow-hidden"
        style={{ background: 'color-mix(in srgb, var(--border) 70%, transparent)' }}
      >
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${p}%`, background: color }}
        />
      </div>
    </div>
  )
}

function IssuesList({ issues }: { issues: DiagnosticsIssue[] }) {
  if (!issues.length) {
    return (
      <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
        No issues detected.
      </div>
    )
  }
  return (
    <ul className="space-y-1.5">
      {issues.map((iss, i) => {
        const color =
          iss.severity === 'error'
            ? 'var(--error, #ef4444)'
            : iss.severity === 'warn'
              ? 'var(--warning, #f59e0b)'
              : 'var(--text-muted)'
        return (
          <li
            key={`${iss.area}-${i}`}
            className="rounded-lg px-2.5 py-2 text-[11px]"
            style={{
              border: `1px solid color-mix(in srgb, ${color} 35%, var(--border))`,
              background: `color-mix(in srgb, ${color} 8%, var(--bg-tertiary))`,
            }}
          >
            <div className="flex items-center gap-1.5 font-semibold" style={{ color }}>
              <span className="uppercase text-[9px] tracking-wide">{iss.severity}</span>
              <span style={{ color: 'var(--text-muted)' }}>·</span>
              <span style={{ color: 'var(--text-secondary)' }}>{iss.area}</span>
            </div>
            <div className="mt-0.5" style={{ color: 'var(--text-primary)' }}>
              {iss.message}
            </div>
            {iss.hint ? (
              <div className="mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {iss.hint}
              </div>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function copyText(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => {
    /* ignore */
  })
}

export function DiagnosticsPanel({ open, onClose }: DiagnosticsPanelProps) {
  const [data, setData] = useState<DiagnosticsSnapshot | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [auto, setAuto] = useState(true)
  const [probe, setProbe] = useState(false)

  const load = useCallback(async () => {
    if (!open) return
    setLoading(true)
    setErr(null)
    try {
      const snap = await getDiagnostics({ probeProviders: probe })
      setData(snap)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [open, probe])

  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

  useEffect(() => {
    if (!open || !auto) return
    const id = window.setInterval(() => {
      void load()
    }, 8000)
    return () => window.clearInterval(id)
  }, [open, auto, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const release = browserStackHold('diagnostics-panel')
    return () => {
      window.removeEventListener('keydown', onKey)
      release()
    }
  }, [open, onClose])

  const remoteProviders = useMemo(() => {
    return (data?.providers?.providers || []).filter((p) => !p.local)
  }, [data])

  const localProviders = useMemo(() => {
    return (data?.providers?.providers || []).filter((p) => p.local && p.connected)
  }, [data])

  if (!open) return null

  const r = data?.remedy
  const rmb = data?.rmb
  const hw = data?.hardware
  const vision = data?.vision
  const computer = data?.computer

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 ui-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="ui-surface w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
        style={{ color: 'var(--text-primary)', background: 'var(--bg-primary)' }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Health diagnostics"
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-3 px-4 py-3 border-b flex-shrink-0"
          style={{ borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)' }}
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="text-sm font-semibold tracking-tight">Diagnostics</div>
              {data ? (
                <span
                  className="text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded"
                  style={{
                    color: overallColor(data.overall),
                    background: `color-mix(in srgb, ${overallColor(data.overall)} 12%, transparent)`,
                    border: `1px solid color-mix(in srgb, ${overallColor(data.overall)} 30%, transparent)`,
                  }}
                >
                  {overallLabel(data.overall)}
                </span>
              ) : null}
            </div>
            <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Remedy · RMB · hardware · hive
              {data?.collect_ms != null ? ` · ${data.collect_ms} ms` : ''}
              {data?.checked_at ? ` · ${data.checked_at.replace('T', ' ').replace('Z', ' UTC')}` : ''}
            </div>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <label
              className="flex items-center gap-1 text-[10px] px-1.5 cursor-pointer"
              style={{ color: 'var(--text-muted)' }}
              title="Refresh every 8s"
            >
              <input
                type="checkbox"
                checked={auto}
                onChange={(e) => setAuto(e.target.checked)}
              />
              Auto
            </label>
            <label
              className="flex items-center gap-1 text-[10px] px-1.5 cursor-pointer"
              style={{ color: 'var(--text-muted)' }}
              title="Measure local provider HTTP latency"
            >
              <input
                type="checkbox"
                checked={probe}
                onChange={(e) => setProbe(e.target.checked)}
              />
              Probe
            </label>
            <button
              type="button"
              className="ui-btn ui-btn-ghost text-xs"
              disabled={loading}
              onClick={() => void load()}
            >
              {loading ? '…' : 'Refresh'}
            </button>
            <button
              type="button"
              className="ui-btn ui-btn-ghost text-xs"
              disabled={!data}
              title="Copy full snapshot JSON"
              onClick={() => data && copyText(JSON.stringify(data, null, 2))}
            >
              Copy JSON
            </button>
            <button type="button" className="ui-btn ui-btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {err && (
            <div
              className="text-xs rounded px-3 py-2"
              style={{ background: 'var(--error)', color: '#fff' }}
            >
              {err}
            </div>
          )}

          {!data && !err && (
            <div className="text-xs py-8 text-center" style={{ color: 'var(--text-muted)' }}>
              Loading diagnostics…
            </div>
          )}

          {data && (
            <>
              <Card title="Issues" subtitle={`${data.issues?.length || 0} findings`}>
                <IssuesList issues={data.issues || []} />
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Card
                  title="Remedy server"
                  subtitle={r?.api?.base_url || 'local API'}
                  badge={
                    <Badge
                      ok={r?.api?.listening !== false}
                      label={r?.api?.listening === false ? 'Down' : 'Up'}
                    />
                  }
                >
                  <KV
                    rows={[
                      ['Version', r?.version],
                      ['Uptime', r?.uptime],
                      ['PID', r?.process?.pid],
                      ['RSS', r?.process?.rss_mb != null ? `${r.process.rss_mb} MB` : null],
                      ['CPU', r?.process?.cpu_pct != null ? `${r.process.cpu_pct}%` : null],
                      ['Memory entries', r?.memory_entries],
                      ['Chat sessions', r?.chat_sessions_count],
                      ['Skills', r?.skills_count],
                      ['Active', `${r?.active_provider || '—'}/${r?.active_model || '—'}`],
                      ['Home', r?.home_dir],
                      [
                        'Home disk free',
                        r?.home_disk?.free_gb != null
                          ? `${r.home_disk.free_gb} GB (${r.home_disk.used_pct ?? '—'}% used)`
                          : null,
                      ],
                    ]}
                  />
                  <Meter pct={r?.home_disk?.used_pct} label="Remedy disk" />
                </Card>

                <Card
                  title="RMB host"
                  subtitle={rmb?.base_url || 'llama-server :8787'}
                  badge={
                    <Badge
                      ok={Boolean(rmb?.running && rmb?.ready)}
                      warn={Boolean(rmb?.enabled && !rmb?.running)}
                      label={
                        rmb?.running
                          ? rmb?.ready
                            ? 'Running'
                            : 'Starting'
                          : rmb?.enabled
                            ? 'Stopped'
                            : 'Off'
                      }
                    />
                  }
                >
                  <KV
                    rows={[
                      ['Model', rmb?.model_name || rmb?.model_id],
                      [
                        'GGUF',
                        rmb?.model_path
                          ? String(rmb.model_path).replace(/^.*[\\/]/, '')
                          : rmb?.model_present
                            ? 'present'
                            : 'missing',
                      ],
                      ['Context', rmb?.ctx_size != null ? String(rmb.ctx_size) : null],
                      ['Profile', rmb?.profile],
                      ['GPU layers', rmb?.n_gpu_layers],
                      ['Latency', rmb?.latency_ms != null ? `${rmb.latency_ms} ms` : null],
                      ['NVIDIA', rmb?.nvidia ? 'yes' : 'no'],
                      ['Runtime', rmb?.runtime_present ? 'ok' : 'missing'],
                      ['Vision suspended', rmb?.vision_suspended ? 'yes' : 'no'],
                    ]}
                  />
                  {rmb?.not_ready_hint ? (
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {rmb.not_ready_hint}
                    </div>
                  ) : null}
                  {rmb?.error ? (
                    <div className="text-[10px]" style={{ color: 'var(--error)' }}>
                      {rmb.error}
                    </div>
                  ) : null}
                </Card>

                <Card
                  title="Hardware"
                  subtitle={hw?.hostname || hw?.platform}
                  badge={
                    <Badge
                      ok={hw?.gpu?.nvidia ? true : null}
                      label={hw?.gpu?.nvidia ? 'NVIDIA' : hw?.system || 'CPU'}
                    />
                  }
                >
                  <KV
                    rows={[
                      ['CPU', hw?.cpu?.brand || `${hw?.cpu?.count_logical ?? '—'} cores`],
                      [
                        'CPU load',
                        hw?.cpu?.percent != null ? `${hw.cpu.percent}%` : null,
                      ],
                      [
                        'RAM',
                        hw?.memory?.total_gb != null
                          ? `${hw.memory.available_gb ?? '—'} / ${hw.memory.total_gb} GB free`
                          : null,
                      ],
                      ['Python', hw?.python],
                      ['Machine', hw?.machine],
                    ]}
                  />
                  <Meter pct={hw?.memory?.used_pct} label="System RAM" />
                  {(hw?.gpu?.gpus || []).map((g, i) => {
                    const tot = Number(g.memory_total_mb || 0)
                    const used = Number(g.memory_used_mb || 0)
                    const pct = tot > 0 ? (100 * used) / tot : null
                    return (
                      <div key={i} className="space-y-1 pt-1">
                        <div className="text-[10px] font-medium truncate" title={g.name}>
                          {g.name}
                          {g.temp_c != null ? ` · ${g.temp_c}°C` : ''}
                          {g.util_pct != null ? ` · ${g.util_pct}% util` : ''}
                        </div>
                        <Meter
                          pct={pct}
                          label={`VRAM ${used.toFixed(0)}/${tot.toFixed(0)} MB`}
                        />
                      </div>
                    )
                  })}
                  {hw?.gpu?.error ? (
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      GPU: {hw.gpu.error}
                    </div>
                  ) : null}
                </Card>

                <Card
                  title="Cloud & local providers"
                  subtitle={
                    data.providers
                      ? `${data.providers.remote_connected_count ?? 0} remote · ${data.providers.local_connected_count ?? 0} local connected`
                      : undefined
                  }
                  badge={
                    <Badge
                      ok={(data.providers?.connected_count || 0) > 0}
                      label={`${data.providers?.connected_count ?? 0} ready`}
                    />
                  }
                >
                  <div className="text-[10px] mb-1.5" style={{ color: 'var(--text-muted)' }}>
                    Active:{' '}
                    <span style={{ color: 'var(--text-primary)' }}>
                      {data.providers?.active?.provider || '—'} /{' '}
                      {data.providers?.active?.model || '—'}
                    </span>
                  </div>
                  <div className="max-h-40 overflow-y-auto space-y-1 pr-0.5">
                    {remoteProviders.map((p) => (
                      <div
                        key={p.id}
                        className="flex items-center justify-between gap-2 text-[11px] py-0.5"
                        style={{
                          borderBottom:
                            '1px solid color-mix(in srgb, var(--border) 50%, transparent)',
                        }}
                      >
                        <div className="min-w-0 flex items-center gap-1.5">
                          <span
                            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                            style={{
                              background: p.connected
                                ? 'var(--success, #22c55e)'
                                : 'var(--text-muted)',
                            }}
                          />
                          <span className="truncate font-medium">{p.label || p.id}</span>
                        </div>
                        <span
                          className="text-[10px] flex-shrink-0 tabular-nums"
                          style={{ color: 'var(--text-muted)' }}
                          title={p.reason}
                        >
                          {p.connected ? p.last_model || p.reason || 'ok' : p.reason || 'off'}
                        </span>
                      </div>
                    ))}
                    {remoteProviders.length === 0 ? (
                      <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        No cloud providers connected.
                      </div>
                    ) : null}
                  </div>
                  {localProviders.length > 0 ? (
                    <div className="pt-1.5">
                      <div
                        className="text-[10px] font-semibold mb-1 uppercase tracking-wide"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        Local
                      </div>
                      {localProviders.map((p) => (
                        <div
                          key={p.id}
                          className="flex justify-between text-[11px] py-0.5"
                        >
                          <span>{p.label || p.id}</span>
                          <span style={{ color: 'var(--text-muted)' }}>
                            {p.last_model || p.reason}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {(data.providers?.usage_7d || []).length > 0 ? (
                    <div className="pt-2">
                      <div
                        className="text-[10px] font-semibold mb-1 uppercase tracking-wide"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        Usage (7d)
                      </div>
                      <table className="w-full text-[10px]">
                        <thead>
                          <tr style={{ color: 'var(--text-muted)' }}>
                            <th className="text-left py-0.5">Provider</th>
                            <th className="text-right">Tokens</th>
                            <th className="text-right">Cost</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(data.providers?.usage_7d || []).map((u) => (
                            <tr
                              key={String(u.provider)}
                              style={{
                                borderTop:
                                  '1px solid color-mix(in srgb, var(--border) 45%, transparent)',
                              }}
                            >
                              <td className="py-1">{u.provider || '—'}</td>
                              <td className="text-right tabular-nums">
                                {formatTokens(Number(u.total_tokens || 0))}
                              </td>
                              <td className="text-right tabular-nums">
                                {formatCost(Number(u.estimated_cost_usd || 0))}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                  {(data.providers?.probes || []).length > 0 ? (
                    <div className="pt-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      Probes:{' '}
                      {(data.providers?.probes || [])
                        .map(
                          (p) =>
                            `${p.id}: ${p.ok ? `${p.latency_ms} ms` : 'fail'}`,
                        )
                        .join(' · ')}
                    </div>
                  ) : null}
                </Card>

                <Card
                  title="Vision (SmolVLM)"
                  subtitle={vision?.base_url || 'local vision'}
                  badge={
                    <Badge
                      ok={Boolean(vision?.running)}
                      warn={Boolean(vision?.suspended_for_rmb)}
                      label={
                        vision?.suspended_for_rmb
                          ? 'Suspended'
                          : vision?.running
                            ? 'Running'
                            : 'Idle'
                      }
                    />
                  }
                >
                  <KV
                    rows={[
                      ['Model', vision?.model_id],
                      ['Enabled', vision?.enabled ? 'yes' : 'no'],
                      ['Ready', vision?.ready ? 'yes' : 'no'],
                      ['Port', vision?.port],
                    ]}
                  />
                  {vision?.not_ready_hint ? (
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {vision.not_ready_hint}
                    </div>
                  ) : null}
                </Card>

                <Card
                  title="Computer host"
                  subtitle="Desktop poller / browser rail"
                  badge={
                    <Badge
                      ok={Boolean(computer?.host_connected)}
                      label={computer?.host_connected ? 'Connected' : 'Offline'}
                    />
                  }
                >
                  <KV
                    rows={[
                      ['Pending jobs', computer?.pending_jobs],
                      ['Jobs root', computer?.jobs_root],
                    ]}
                  />
                  {computer?.error ? (
                    <div className="text-[10px]" style={{ color: 'var(--error)' }}>
                      {computer.error}
                    </div>
                  ) : null}
                </Card>
              </div>
            </>
          )}

          <HiveInspector open={open} />
        </div>
      </div>
    </div>
  )
}
