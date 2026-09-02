import { useCallback, useEffect, useRef, useState } from 'react'
import {
  assignHiveDaughter,
  getHiveRoster,
  hiveIsLive,
  retireHiveDaughter,
  spawnHiveDaughter,
  type HiveRosterRow,
} from '../../api/hive'

function StatusDot({ status }: { status: string }) {
  const COLOR: Record<string, string> = {
    pending: 'var(--text-muted)', running: '#22c55e', reported: 'var(--accent)',
    asleep: '#facc15', blocked: 'var(--error)', retired: 'var(--text-muted)',
    cancelled: 'var(--error)',
  }
  const color = COLOR[status] ?? 'var(--text-muted)'
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
      background: color, flexShrink: 0,
      boxShadow: status === 'running'
        ? `0 0 0 2px color-mix(in srgb, ${color} 30%, transparent)` : undefined,
    }} title={status} />
  )
}

export function AutomationsSlide() {
  const [roster, setRoster] = useState<HiveRosterRow[]>([])
  const [liveStats, setLiveStats] = useState({ posts: 0, foragers: 0 })
  const [rosterErr, setRosterErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [goal, setGoal] = useState('')
  const [cadence, setCadence] = useState<'forager' | 'post'>('forager')
  const [budgetSteps, setBudgetSteps] = useState(8)
  const [pulseS, setPulseS] = useState(60)
  const [spawning, setSpawning] = useState(false)
  const [spawnMsg, setSpawnMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [confirmRetire, setConfirmRetire] = useState<string | null>(null)
  const [assignTarget, setAssignTarget] = useState<string | null>(null)
  const [assignDraft, setAssignDraft] = useState('')
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  const [actionErr, setActionErr] = useState<string | null>(null)
  const [showRetired, setShowRetired] = useState(false)
  const goalRef = useRef<HTMLTextAreaElement>(null)

  const loadRoster = useCallback(async () => {
    try {
      const data = await getHiveRoster()
      setRoster(data.daughters)
      setLiveStats({ posts: data.live_posts, foragers: data.live_foragers })
      setRosterErr(null)
    } catch (e) {
      setRosterErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    void loadRoster().finally(() => setLoading(false))
    const id = window.setInterval(() => void loadRoster(), 6000)
    return () => clearInterval(id)
  }, [loadRoster])

  const handleSpawn = async () => {
    const g = goal.trim()
    if (!g) return
    setSpawning(true); setSpawnMsg(null)
    try {
      const res = await spawnHiveDaughter({
        goal: g, cadence, budget_steps: budgetSteps,
        pulse_s: cadence === 'post' ? pulseS : 0,
      })
      if (!res.ok) setSpawnMsg({ ok: false, text: res.error ?? 'spawn failed' })
      else {
        setSpawnMsg({ ok: true, text: `${res.hive_id?.slice(0, 8)} spawned (${res.cadence})` })
        setGoal(''); await loadRoster()
      }
    } catch (e) {
      setSpawnMsg({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally { setSpawning(false) }
  }

  const handleRetire = async (id: string) => {
    if (confirmRetire !== id) { setConfirmRetire(id); return }
    setActionBusy(id); setActionErr(null)
    try {
      const res = await retireHiveDaughter(id)
      if (!res.ok) setActionErr(res.error ?? 'retire failed')
      setConfirmRetire(null); await loadRoster()
    } catch (e) { setActionErr(e instanceof Error ? e.message : String(e)) }
    finally { setActionBusy(null) }
  }

  const handleAssign = async (id: string) => {
    const g = assignDraft.trim()
    if (!g) return
    setActionBusy(id); setActionErr(null)
    try {
      const res = await assignHiveDaughter(id, g)
      if (!res.ok) setActionErr(res.error ?? 'assign failed')
      setAssignTarget(null); setAssignDraft(''); await loadRoster()
    } catch (e) { setActionErr(e instanceof Error ? e.message : String(e)) }
    finally { setActionBusy(null) }
  }

  const displayed = showRetired ? roster : roster.filter(hiveIsLive)

  const STATUS_COLOR: Record<string, string> = {
    pending: 'var(--text-muted)', running: '#22c55e', reported: 'var(--accent)',
    asleep: '#facc15', blocked: 'var(--error)', retired: 'var(--text-muted)', cancelled: 'var(--error)',
  }

  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)' }}>

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 shrink-0 border-b text-[11px]"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.04em' }}>HIVE</span>
        <span className="px-1.5 py-0.5 rounded font-mono font-semibold text-[10px]"
          style={{ background: 'color-mix(in srgb,#22c55e 18%,transparent)', color: '#22c55e' }}>
          {liveStats.foragers} foraging
        </span>
        <span className="px-1.5 py-0.5 rounded font-mono font-semibold text-[10px]"
          style={{ background: 'color-mix(in srgb,var(--accent) 18%,transparent)', color: 'var(--accent)' }}>
          {liveStats.posts} standing
        </span>
        {loading
          ? <span style={{ color: 'var(--text-muted)', marginLeft: 'auto', fontSize: 10 }}>syncing…</span>
          : <button type="button" onClick={() => void loadRoster()}
              className="ml-auto text-[10px] px-1.5 py-0.5 rounded"
              style={{ color: 'var(--text-muted)', border: '1px solid var(--border)', background: 'var(--bg-primary)' }}>↺</button>
        }
      </div>

      {/* Spawn form */}
      <div className="px-3 pt-2 pb-2 shrink-0 space-y-2 border-b" style={{ borderColor: 'var(--border)' }}>
        <textarea ref={goalRef} value={goal} onChange={e => setGoal(e.target.value)}
          placeholder="Describe what this hive daughter should do…"
          rows={2}
          className="w-full rounded px-2 py-1.5 text-[12px] resize-none outline-none"
          style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)', lineHeight: 1.45 }}
          onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void handleSpawn() } }}
        />
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-1">
            {(['forager', 'post'] as const).map(c => (
              <button key={c} type="button" onClick={() => setCadence(c)}
                className="px-2 py-1 rounded text-[10px] font-semibold"
                style={{
                  background: cadence === c ? 'var(--accent)' : 'var(--bg-primary)',
                  color: cadence === c ? '#fff' : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}>
                {c === 'forager' ? '⚡ One-shot' : '🔁 Standing'}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Steps
            <input type="number" min={1} max={16} value={budgetSteps}
              onChange={e => setBudgetSteps(Math.max(1, Math.min(16, Number(e.target.value))))}
              className="w-10 rounded px-1 py-0.5 text-[10px] text-center outline-none"
              style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
          </label>
          {cadence === 'post' && (
            <label className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
              Pulse (s)
              <input type="number" min={30} max={3600} step={30} value={pulseS}
                onChange={e => setPulseS(Math.max(30, Number(e.target.value)))}
                className="w-14 rounded px-1 py-0.5 text-[10px] text-center outline-none"
                style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </label>
          )}
          <button type="button" disabled={spawning || !goal.trim()} onClick={() => void handleSpawn()}
            className="ml-auto px-3 py-1.5 rounded text-[11px] font-semibold disabled:opacity-40"
            style={{ background: 'var(--accent)', color: '#fff', border: 'none' }}>
            {spawning ? 'Spawning…' : '⚡ Spawn'}
          </button>
        </div>
        {spawnMsg && (
          <div className="text-[10px] px-1" style={{ color: spawnMsg.ok ? '#22c55e' : 'var(--error)' }}>
            {spawnMsg.ok ? '✓ ' : '✗ '}{spawnMsg.text}
          </div>
        )}
      </div>

      {/* Roster */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex items-center gap-2 px-3 py-1.5 sticky top-0 border-b text-[10px]"
          style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)', zIndex: 2 }}>
          <span style={{ color: 'var(--text-muted)' }}>{displayed.length} daughter{displayed.length !== 1 ? 's' : ''}</span>
          <label className="flex items-center gap-1 cursor-pointer ml-auto" style={{ color: 'var(--text-muted)' }}>
            <input type="checkbox" checked={showRetired} onChange={e => setShowRetired(e.target.checked)} />
            Retired
          </label>
        </div>

        {rosterErr && <div className="px-3 py-2 text-[11px]" style={{ color: 'var(--error)' }}>{rosterErr}</div>}
        {actionErr && <div className="px-3 py-1 text-[10px]" style={{ color: 'var(--error)' }}>{actionErr}</div>}
        {displayed.length === 0 && !rosterErr && (
          <div className="px-3 py-6 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>
            No active daughters — spawn one above.
          </div>
        )}

        {displayed.map(row => {
          const live = hiveIsLive(row)
          const isRetiring = confirmRetire === row.id
          const isAssigning = assignTarget === row.id
          const rowBusy = actionBusy === row.id
          const isPost = row.cadence === 'post'

          return (
            <div key={row.id} className="px-3 py-2.5 border-b"
              style={{ borderColor: 'var(--border)', opacity: live ? 1 : 0.5 }}>
              <div className="flex items-start gap-2 min-w-0">
                <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
                  <StatusDot status={row.status} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium leading-snug truncate"
                    style={{ color: 'var(--text-primary)' }} title={row.goal}>
                    {row.goal || 'untitled'}
                  </p>
                  <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5 text-[10px]"
                    style={{ color: 'var(--text-muted)' }}>
                    <span>{isPost ? '🔁 Standing' : '⚡ One-shot'}</span>
                    <span style={{ color: STATUS_COLOR[row.status] ?? 'inherit' }}>{row.status}</span>
                    <span className="font-mono opacity-70">{row.id.slice(0, 8)}</span>
                    {(row.pulse_count ?? 0) > 0 && <span>{row.pulse_count} pulse{row.pulse_count !== 1 ? 's' : ''}</span>}
                    {row.pulse_s > 0 && <span>every {row.pulse_s}s</span>}
                  </div>
                  {row.outcome && (
                    <p className="mt-1 text-[11px] leading-snug line-clamp-2"
                      style={{ color: 'var(--text-secondary)' }} title={row.outcome}>
                      {row.outcome}
                    </p>
                  )}
                  {row.blockers?.length > 0 && (
                    <p className="mt-0.5 text-[10px]" style={{ color: 'var(--error)' }}>
                      ⚠ {row.blockers.join(' · ')}
                    </p>
                  )}
                </div>
                {live && (
                  <div className="flex flex-col gap-1 shrink-0">
                    {isPost && (
                      <button type="button" disabled={rowBusy}
                        onClick={() => { setAssignTarget(isAssigning ? null : row.id); setAssignDraft(row.goal); setConfirmRetire(null) }}
                        className="text-[9px] px-1.5 py-0.5 rounded font-semibold"
                        style={{ background: 'color-mix(in srgb,var(--accent) 16%,transparent)', color: 'var(--accent)', border: '1px solid color-mix(in srgb,var(--accent) 35%,transparent)' }}>
                        Reassign
                      </button>
                    )}
                    <button type="button" disabled={rowBusy}
                      onClick={() => { setAssignTarget(null); void handleRetire(row.id) }}
                      className="text-[9px] px-1.5 py-0.5 rounded font-semibold"
                      style={{
                        background: isRetiring ? 'var(--error)' : 'color-mix(in srgb,var(--error) 12%,transparent)',
                        color: isRetiring ? '#fff' : 'var(--error)',
                        border: `1px solid color-mix(in srgb,var(--error) ${isRetiring ? 100 : 35}%,transparent)`,
                      }}>
                      {rowBusy ? '…' : isRetiring ? 'Confirm' : 'Retire'}
                    </button>
                  </div>
                )}
              </div>
              {isAssigning && (
                <div className="mt-2 flex gap-1.5">
                  <input value={assignDraft} onChange={e => setAssignDraft(e.target.value)}
                    placeholder="New goal…"
                    className="flex-1 rounded px-2 py-1 text-[11px] outline-none"
                    style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                    autoFocus
                    onKeyDown={e => { if (e.key === 'Enter') void handleAssign(row.id); if (e.key === 'Escape') setAssignTarget(null) }} />
                  <button type="button" disabled={rowBusy || !assignDraft.trim()} onClick={() => void handleAssign(row.id)}
                    className="px-2 py-1 rounded text-[10px] font-semibold disabled:opacity-40"
                    style={{ background: 'var(--accent)', color: '#fff' }}>Save</button>
                  <button type="button" onClick={() => setAssignTarget(null)}
                    className="px-1.5 py-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>×</button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
