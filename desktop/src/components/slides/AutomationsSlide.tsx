import { EmptyState } from '../EmptyState'
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
  const spawnInFlight = useRef(false)
  const actionInFlight = useRef(false)
  const rosterGeneration = useRef(0)

  const loadRoster = useCallback(async () => {
    const generation = ++rosterGeneration.current
    try {
      const data = await getHiveRoster()
      if (generation !== rosterGeneration.current) return
      setRoster(data.daughters)
      setLiveStats({ posts: data.live_posts, foragers: data.live_foragers })
      setRosterErr(null)
    } catch (e) {
      if (generation === rosterGeneration.current) setRosterErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    void loadRoster().finally(() => setLoading(false))
    const id = window.setInterval(() => void loadRoster(), 6000)
    return () => { clearInterval(id); rosterGeneration.current += 1 }
  }, [loadRoster])

  const handleSpawn = async () => {
    const g = goal.trim()
    if (!g || spawnInFlight.current) return
    spawnInFlight.current = true
    setSpawning(true); setSpawnMsg(null)
    try {
      const res = await spawnHiveDaughter({
        goal: g, cadence, budget_steps: budgetSteps,
        pulse_s: cadence === 'post' ? pulseS : 0,
      })
      if (!res.ok) setSpawnMsg({ ok: false, text: res.error ?? 'spawn failed' })
      else {
        setSpawnMsg({ ok: true, text: cadence === 'post' ? 'Repeating task started.' : 'Task started.' })
        setGoal(''); await loadRoster()
      }
    } catch (e) {
      setSpawnMsg({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally { spawnInFlight.current = false; setSpawning(false) }
  }

  const handleRetire = async (id: string) => {
    if (actionInFlight.current) return
    if (confirmRetire !== id) { setConfirmRetire(id); return }
    actionInFlight.current = true
    setActionBusy(id); setActionErr(null)
    try {
      const res = await retireHiveDaughter(id)
      if (!res.ok) { setActionErr(res.error ?? 'Could not stop this task.'); return }
      setConfirmRetire(null); await loadRoster()
    } catch (e) { setActionErr(e instanceof Error ? e.message : String(e)) }
    finally { actionInFlight.current = false; setActionBusy(null) }
  }

  const handleAssign = async (id: string) => {
    const g = assignDraft.trim()
    if (!g || actionInFlight.current) return
    actionInFlight.current = true
    setActionBusy(id); setActionErr(null)
    try {
      const res = await assignHiveDaughter(id, g)
      if (!res.ok) { setActionErr(res.error ?? 'Could not update this task.'); return }
      setAssignTarget(null); setAssignDraft(''); await loadRoster()
    } catch (e) { setActionErr(e instanceof Error ? e.message : String(e)) }
    finally { actionInFlight.current = false; setActionBusy(null) }
  }

  const displayed = showRetired ? roster : roster.filter(hiveIsLive)

  const STATUS_COLOR: Record<string, string> = {
    pending: 'var(--text-muted)', running: '#22c55e', reported: 'var(--accent)',
    asleep: '#facc15', blocked: 'var(--error)', retired: 'var(--text-muted)', cancelled: 'var(--error)',
  }

  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)' }}>

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 shrink-0 border-b text-xs"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.04em' }}>BACKGROUND TASKS</span>
        <span className="px-1.5 py-0.5 rounded font-mono font-semibold text-xs"
          style={{ background: 'color-mix(in srgb,#22c55e 18%,transparent)', color: '#22c55e' }}>
          {liveStats.foragers} one-time
        </span>
        <span className="px-1.5 py-0.5 rounded font-mono font-semibold text-xs"
          style={{ background: 'color-mix(in srgb,var(--accent) 18%,transparent)', color: 'var(--accent)' }}>
          {liveStats.posts} repeating
        </span>
        {loading
          ? <span style={{ color: 'var(--text-muted)', marginLeft: 'auto', fontSize: 10 }}>syncing…</span>
          : <button type="button" aria-label="Refresh tasks" title="Refresh tasks" onClick={() => void loadRoster()}
              className="ml-auto text-xs px-1.5 py-0.5 rounded"
              style={{ color: 'var(--text-muted)', border: '1px solid var(--border)', background: 'var(--bg-primary)' }}>↺</button>
        }
      </div>

      {/* Spawn form */}
      <div className="px-3 pt-2 pb-2 shrink-0 space-y-2 border-b" style={{ borderColor: 'var(--border)' }}>
        <textarea ref={goalRef} value={goal} onChange={e => setGoal(e.target.value)}
          aria-label="Task instructions"
          placeholder="What should Remedy work on in the background?"
          rows={2}
          className="w-full rounded px-2 py-1.5 text-sm resize-none outline-none"
          style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)', lineHeight: 1.45 }}
          onKeyDown={e => { if (!e.nativeEvent.isComposing && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void handleSpawn() } }}
        />
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-1">
            {(['forager', 'post'] as const).map(c => (
              <button key={c} type="button" aria-pressed={cadence === c} onClick={() => setCadence(c)}
                className="px-2 py-1 rounded text-xs font-semibold"
                style={{
                  background: cadence === c ? 'var(--accent)' : 'var(--bg-primary)',
                  color: cadence === c ? 'var(--accent-foreground)' : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}>
                {c === 'forager' ? 'Run once' : 'Repeat'}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            Steps
            <input type="number" min={1} max={16} value={budgetSteps}
              onChange={e => setBudgetSteps(Math.max(1, Math.min(16, Number(e.target.value))))}
              className="w-10 rounded px-1 py-0.5 text-xs text-center outline-none"
              style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
          </label>
          {cadence === 'post' && (
            <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
              Every (seconds)
              <input type="number" min={30} max={3600} step={30} value={pulseS}
                onChange={e => setPulseS(Math.max(30, Math.min(3600, Math.trunc(Number(e.target.value)) || 60)))}
                className="w-14 rounded px-1 py-0.5 text-xs text-center outline-none"
                style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </label>
          )}
          <button type="button" disabled={spawning || !goal.trim()} onClick={() => void handleSpawn()}
            className="ml-auto px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-40"
            style={{ background: 'var(--accent)', color: 'var(--accent-foreground)', border: 'none' }}>
            {spawning ? 'Starting…' : 'Start task'}
          </button>
        </div>
        {spawnMsg && (
          <div role="status" className="text-xs px-1" style={{ color: spawnMsg.ok ? '#22c55e' : 'var(--error)' }}>
            {spawnMsg.ok ? '✓ ' : '✗ '}{spawnMsg.text}
          </div>
        )}
      </div>

      {/* Roster */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex items-center gap-2 px-3 py-1.5 sticky top-0 border-b text-xs"
          style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)', zIndex: 2 }}>
          <span style={{ color: 'var(--text-muted)' }}>{displayed.length} task{displayed.length !== 1 ? 's' : ''}</span>
          <label className="flex items-center gap-1 cursor-pointer ml-auto" style={{ color: 'var(--text-muted)' }}>
            <input type="checkbox" checked={showRetired} onChange={e => setShowRetired(e.target.checked)} />
            Include stopped
          </label>
        </div>

        {rosterErr && <div role="alert" className="px-3 py-2 text-xs" style={{ color: 'var(--error)' }}>{rosterErr}</div>}
        {actionErr && <div role="alert" className="px-3 py-1 text-xs" style={{ color: 'var(--error)' }}>{actionErr}</div>}
        {displayed.length === 0 && !rosterErr && !loading && (
          <EmptyState compact title="No background tasks yet" description="Give Remedy a task above. Run it once, or repeat it at an interval." />
        )}

        {displayed.map(row => {
          const live = hiveIsLive(row)
          const isRetiring = confirmRetire === row.id
          const isAssigning = assignTarget === row.id
          const rowBusy = actionBusy !== null
          const isPost = row.cadence === 'post'

          return (
            <div key={row.id} className="px-3 py-2.5 border-b"
              style={{ borderColor: 'var(--border)', opacity: live ? 1 : 0.5 }}>
              <div className="flex items-start gap-2 min-w-0">
                <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
                  <StatusDot status={row.status} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium leading-snug truncate"
                    style={{ color: 'var(--text-primary)' }} title={row.goal}>
                    {row.goal || 'untitled'}
                  </p>
                  <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5 text-xs"
                    style={{ color: 'var(--text-muted)' }}>
                    <span>{isPost ? 'Repeating' : 'One-time'}</span>
                    <span style={{ color: STATUS_COLOR[row.status] ?? 'inherit' }}>{row.status}</span>
                    <span className="font-mono opacity-70">{row.id.slice(0, 8)}</span>
                    {(row.pulse_count ?? 0) > 0 && <span>{row.pulse_count} pulse{row.pulse_count !== 1 ? 's' : ''}</span>}
                    {row.pulse_s > 0 && <span>every {row.pulse_s}s</span>}
                  </div>
                  {row.outcome && (
                    <p className="mt-1 text-xs leading-snug line-clamp-2"
                      style={{ color: 'var(--text-secondary)' }} title={row.outcome}>
                      {row.outcome}
                    </p>
                  )}
                  {row.blockers?.length > 0 && (
                    <p className="mt-0.5 text-xs" style={{ color: 'var(--error)' }}>
                      ⚠ {row.blockers.join(' · ')}
                    </p>
                  )}
                </div>
                {live && (
                  <div className="flex flex-col gap-1 shrink-0">
                    {isPost && (
                      <button type="button" disabled={rowBusy}
                        onClick={() => { setAssignTarget(isAssigning ? null : row.id); setAssignDraft(row.goal); setConfirmRetire(null) }}
                        className="text-xs px-1.5 py-0.5 rounded font-semibold"
                        style={{ background: 'color-mix(in srgb,var(--accent) 16%,transparent)', color: 'var(--accent)', border: '1px solid color-mix(in srgb,var(--accent) 35%,transparent)' }}>
                        Reassign
                      </button>
                    )}
                    <button type="button" disabled={rowBusy}
                      onClick={() => { setAssignTarget(null); void handleRetire(row.id) }}
                      className="text-xs px-1.5 py-0.5 rounded font-semibold"
                      style={{
                        background: isRetiring ? 'var(--error)' : 'color-mix(in srgb,var(--error) 12%,transparent)',
                        color: isRetiring ? 'var(--error-foreground)' : 'var(--error)',
                        border: `1px solid color-mix(in srgb,var(--error) ${isRetiring ? 100 : 35}%,transparent)`,
                      }}>
                      {rowBusy ? '…' : isRetiring ? 'Confirm stop' : 'Stop'}
                    </button>
                  </div>
                )}
              </div>
              {isAssigning && (
                <div className="mt-2 flex gap-1.5">
                  <input value={assignDraft} onChange={e => setAssignDraft(e.target.value)}
                    aria-label="Updated task instructions"
                    placeholder="New goal…"
                    className="flex-1 rounded px-2 py-1 text-xs outline-none"
                    style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                    autoFocus
                    onKeyDown={e => { if (e.nativeEvent.isComposing) return; if (e.key === 'Enter') void handleAssign(row.id); if (e.key === 'Escape') { e.preventDefault(); setAssignTarget(null) } }} />
                  <button type="button" disabled={rowBusy || !assignDraft.trim()} onClick={() => void handleAssign(row.id)}
                    className="px-2 py-1 rounded text-xs font-semibold disabled:opacity-40"
                    style={{ background: 'var(--accent)', color: 'var(--accent-foreground)' }}>Save</button>
                  <button type="button" aria-label="Cancel task edit" onClick={() => setAssignTarget(null)}
                    className="px-1.5 py-1 text-xs" style={{ color: 'var(--text-muted)' }}>×</button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
