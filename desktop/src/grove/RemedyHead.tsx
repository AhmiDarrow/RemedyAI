/** Remedy at the head of the house — Grove's always-on panel.
 *
 * Remedy is one partner, not a fleet: her card leads, her children (side
 * jobs she started) hang beneath her like a branch, then her standing
 * routines, then what happened while the owner was away. Everything here
 * reads one overview (`/partner/head`) and every control is one click. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createRoutine,
  deleteRoutine,
  forgetChild,
  markActivitySeen,
  needsOwner,
  patchRoutine,
  relativeWhen,
  runHeartbeatNow,
  runRoutineNow,
  scheduleFromChoice,
  setHeartbeat,
  setOwnerChannel,
  spawnChild,
  stateWords,
  stopChild,
  type HeadActivity,
  type HeadChild,
  type HeadOverview,
  type HeadRoutine,
  type HeadState,
} from '../api/head'
import { RemedyLogo } from '../components/RemedyLogo'
import './grove-head.css'

export interface RemedyHeadProps {
  overview: HeadOverview | null
  partnerName: string
  onChanged: () => void
  onOpenChild: (child: HeadChild) => void
  onOpenHead: () => void
  onOpenSession: (sessionId: string) => void
  /** head = Remedy and her side jobs (top of Grove); house = her routines and
   * what happened while you were away (below your goals). */
  part?: 'head' | 'house'
}

function StateDot({ state }: { state: HeadState | '' | undefined }) {
  return <span className={`rh-dot s-${state || 'idle'}`} aria-hidden />
}

export function RemedyHead({
  overview,
  partnerName,
  onChanged,
  onOpenChild,
  onOpenHead,
  onOpenSession,
  part = 'head',
}: RemedyHeadProps) {
  const name = partnerName || 'Remedy'
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [jobOpen, setJobOpen] = useState(false)
  const [routineOpen, setRoutineOpen] = useState(false)
  const [showAllActivity, setShowAllActivity] = useState(false)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')

  const run = useCallback(
    async (fn: () => Promise<unknown>, ok?: string) => {
      setErr('')
      try {
        await fn()
        if (ok) setNote(ok)
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'That did not go through — try again.')
      }
      onChanged()
    },
    [onChanged],
  )

  // The timeline counts as seen once it has been on screen for a moment.
  const unseen = overview?.activity_unseen || 0
  useEffect(() => {
    if (!unseen || part !== 'house') return
    const t = setTimeout(
      () =>
        void markActivitySeen()
          .then(onChanged)
          .catch(() => {}),
      4000,
    )
    return () => clearTimeout(t)
  }, [unseen, onChanged, part])

  useEffect(() => {
    if (!note) return
    const t = setTimeout(() => setNote(''), 5000)
    return () => clearTimeout(t)
  }, [note])

  const children = useMemo(() => overview?.children || [], [overview])
  const liveChildren = children.filter((c) => !['done', 'stopped', 'error'].includes(c.state))
  const restingChildren = children.filter((c) => ['done', 'stopped', 'error'].includes(c.state))
  const needChildren = children.filter((c) => needsOwner(c.state)).length
  const hb = overview?.heartbeat
  const cap = overview?.capacity
  const headState: HeadState = overview?.head.state || 'idle'
  const anyWorking = headState === 'working' || children.some((c) => c.state === 'working')

  const statusLine = useMemo(() => {
    if (!overview) return 'Waking up…'
    if (anyWorking) {
      const n = children.filter((c) => c.state === 'working').length
      return n > 0 ? `Working — ${n} side job${n > 1 ? 's' : ''} going` : 'Working on something for you'
    }
    if (needChildren)
      return `${needChildren} side job${needChildren > 1 ? 's' : ''} need${needChildren > 1 ? '' : 's'} you`
    if (hb?.enabled && hb.last_note) return hb.last_note
    if (hb?.enabled) return 'Keeping watch'
    return 'Here when you need me'
  }, [overview, anyWorking, children, needChildren, hb])

  const activity = overview?.activity || []
  const shownActivity = showAllActivity ? activity : activity.slice(0, 5)

  return (
    <section
      className={`rh rh-part-${part}`}
      aria-label={part === 'head' ? `${name} and her side jobs` : `${name}'s routines and recent activity`}
    >
      {part === 'head' && (
        <>
          {/* ── the head ─────────────────────────────────────────── */}
          <div
            className={`rh-head s-${anyWorking ? 'working' : needChildren ? 'waiting' : hb?.enabled ? 'watch' : 'idle'}`}
          >
            <button type="button" className="rh-avatar" onClick={onOpenHead} title={`Open ${name}'s own conversation`}>
              <span className="rh-ring" aria-hidden />
              <RemedyLogo size={26} variant="auto" title={name} />
            </button>
            <div className="rh-headtext">
              <div className="rh-name">
                {name}
                <span className="rh-tag">{hb?.enabled ? 'Always on' : 'On call'}</span>
              </div>
              <div className="rh-status" role="status" aria-live="polite">
                {statusLine}
              </div>
            </div>
            <div className="rh-headactions">
              <label
                className="rh-switch"
                title="Remedy looks around on her own and tells you what changed. Uses no AI — it runs on your PC."
              >
                <input
                  type="checkbox"
                  checked={!!hb?.enabled}
                  onChange={(e) =>
                    void run(
                      () => setHeartbeat({ enabled: e.target.checked }),
                      e.target.checked ? 'Remedy will keep watch.' : 'Remedy stopped keeping watch.',
                    )
                  }
                />
                <span className="rh-switch-track" aria-hidden />
                <span>Keep watch</span>
              </label>
              <button
                type="button"
                className="rh-icon"
                aria-expanded={settingsOpen}
                aria-label="Always-on settings"
                title="Always-on settings"
                onClick={() => setSettingsOpen((v) => !v)}
              >
                ⋯
              </button>
            </div>
          </div>

          {settingsOpen && overview && (
            <HeadSettings overview={overview} onDone={() => setSettingsOpen(false)} run={run} />
          )}

          {(note || err) && (
            <div className={`rh-toast${err ? ' err' : ''}`} role={err ? 'alert' : 'status'}>
              {err || note}
            </div>
          )}

          {/* ── her children ─────────────────────────────────────── */}
          <div className="rh-branch">
            {liveChildren.map((c) => (
              <ChildRow
                key={c.child_id}
                child={c}
                onOpen={() => onOpenChild(c)}
                onStop={() => void run(() => stopChild(c.child_id), `${c.label} stopped.`)}
              />
            ))}
            {restingChildren.slice(0, 4).map((c) => (
              <ChildRow
                key={c.child_id}
                child={c}
                onOpen={() => onOpenChild(c)}
                onDismiss={() => void run(() => forgetChild(c.child_id))}
              />
            ))}
            {jobOpen ? (
              <SideJobForm
                onCancel={() => setJobOpen(false)}
                onStart={async (task, label) => {
                  await run(async () => {
                    const r = await spawnChild({ task, label })
                    setNote(r.capacity_note)
                  })
                  setJobOpen(false)
                }}
              />
            ) : (
              <div className="rh-leaf add">
                <span className="rh-stem" aria-hidden />
                <button type="button" className="rh-link" onClick={() => setJobOpen(true)}>
                  + Give {name} a side job
                </button>
                {cap && (
                  <span className={`rh-cap${cap.paused ? ' paused' : ''}`} title={overview?.capacity_note}>
                    {cap.paused ? 'PC busy — jobs wait' : `${Math.max(0, cap.slots - cap.busy)} of ${cap.slots} free`}
                  </span>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {part === 'house' && (note || err) && (
        <div className={`rh-toast${err ? ' err' : ''}`} role={err ? 'alert' : 'status'}>
          {err || note}
        </div>
      )}

      {part === 'house' && (
        <>
          {/* ── routines ─────────────────────────────────────────── */}
          <div className="rh-block">
            <div className="rh-blockhead">
              <h3>Routines</h3>
              <button type="button" className="rh-link" onClick={() => setRoutineOpen(true)}>
                + New
              </button>
            </div>
            {(overview?.routines || []).length === 0 ? (
              <p className="rh-empty">
                Standing jobs on a clock — “every weekday at 7, brief me”. Reminders cost nothing; tasks have {name} do
                the work.
              </p>
            ) : (
              <ul className="rh-routines">
                {(overview?.routines || []).map((r) => (
                  <RoutineRow
                    key={r.routine_id}
                    r={r}
                    onToggle={(on) => void run(() => patchRoutine(r.routine_id, { enabled: on }))}
                    onRun={() => void run(() => runRoutineNow(r.routine_id), `${r.title} — on it.`)}
                    onDelete={() => {
                      if (window.confirm(`Retire “${r.title}”?`)) void run(() => deleteRoutine(r.routine_id))
                    }}
                    onOpen={
                      r.target === 'head' && overview?.head.session_id
                        ? () => onOpenSession(overview.head.session_id)
                        : undefined
                    }
                  />
                ))}
              </ul>
            )}
          </div>

          {/* ── while you were away ──────────────────────────────── */}
          {activity.length > 0 && (
            <div className="rh-block">
              <div className="rh-blockhead">
                <h3>
                  While you were away
                  {unseen > 0 && <span className="rh-badge">{unseen > 99 ? '99+' : unseen}</span>}
                </h3>
                <button
                  type="button"
                  className="rh-link"
                  onClick={() => void run(() => runHeartbeatNow())}
                  title="Look around now (no AI used)"
                >
                  Check now
                </button>
              </div>
              <ol className="rh-timeline">
                {shownActivity.map((a) => (
                  <ActivityRow key={a.id} a={a} onOpenSession={onOpenSession} />
                ))}
              </ol>
              {activity.length > 5 && (
                <button type="button" className="rh-link small" onClick={() => setShowAllActivity((v) => !v)}>
                  {showAllActivity ? 'Show less' : `Show all ${activity.length}`}
                </button>
              )}
            </div>
          )}

          {routineOpen && (
            <RoutineDialog
              name={name}
              canPhone={!!overview?.channels.owner}
              onClose={() => setRoutineOpen(false)}
              onCreate={async (body) => {
                await createRoutine(body)
                setRoutineOpen(false)
                setNote('Routine saved.')
                onChanged()
              }}
            />
          )}
        </>
      )}
    </section>
  )
}

// ── pieces ──────────────────────────────────────────────────────────────

function ChildRow({
  child,
  onOpen,
  onStop,
  onDismiss,
}: {
  child: HeadChild
  onOpen: () => void
  onStop?: () => void
  onDismiss?: () => void
}) {
  return (
    <div className={`rh-leaf s-${child.state}`} data-testid="rh-child">
      <span className="rh-stem" aria-hidden />
      <button type="button" className="rh-leafmain" onClick={onOpen} aria-label={`Open side job: ${child.label}`}>
        <span className="rh-sprout" aria-hidden>
          🌱
          <StateDot state={child.state} />
        </span>
        <span className="rh-leaftext">
          <span className="rh-leaflabel">{child.label}</span>
          <span className="rh-leafsub">
            <b>{stateWords(child.state)}</b>
            {child.last_report ? ` · ${child.last_report}` : ''}
          </span>
        </span>
      </button>
      {onStop && child.state !== 'done' && (
        <button type="button" className="rh-icon" onClick={onStop} aria-label={`Stop ${child.label}`} title="Stop">
          ■
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          className="rh-icon"
          onClick={onDismiss}
          aria-label={`Clear ${child.label}`}
          title="Clear from the list"
        >
          ×
        </button>
      )}
    </div>
  )
}

function SideJobForm({
  onStart,
  onCancel,
}: {
  onStart: (task: string, label: string) => Promise<void>
  onCancel: () => void
}) {
  const [task, setTask] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="rh-leaf form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!task.trim() || busy) return
        setBusy(true)
        void onStart(task.trim(), label.trim()).finally(() => setBusy(false))
      }}
    >
      <span className="rh-stem" aria-hidden />
      <div className="rh-formbody">
        <textarea
          autoFocus
          rows={2}
          placeholder="What should this side job do? Be complete — it works on its own and reports back."
          value={task}
          onChange={(e) => setTask(e.target.value)}
          aria-label="Side job"
        />
        <div className="rh-formrow">
          <input
            placeholder="Short name (optional)"
            value={label}
            maxLength={60}
            onChange={(e) => setLabel(e.target.value)}
            aria-label="Short name"
          />
          <button type="button" className="rh-link" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="rh-btn" disabled={!task.trim() || busy}>
            Start
          </button>
        </div>
      </div>
    </form>
  )
}

function RoutineRow({
  r,
  onToggle,
  onRun,
  onDelete,
  onOpen,
}: {
  r: HeadRoutine
  onToggle: (on: boolean) => void
  onRun: () => void
  onDelete: () => void
  onOpen?: () => void
}) {
  const next = r.enabled ? relativeWhen(r.next_run) : ''
  return (
    <li className={`rh-routine${r.enabled ? '' : ' off'}`}>
      <span className="rh-routine-ico" aria-hidden>
        {r.mode === 'remind' ? '⏰' : '🔁'}
      </span>
      <button type="button" className="rh-routine-main" onClick={onOpen} disabled={!onOpen}>
        <span className="rh-leaflabel">{r.title}</span>
        <span className="rh-leafsub">
          {r.when}
          {next ? ` · next ${next}` : ''}
          {r.last_state && r.last_state !== 'done' ? ` · ${stateWords(r.last_state)}` : ''}
        </span>
      </button>
      <label className="rh-switch mini" title={r.enabled ? 'Pause' : 'Resume'}>
        <input
          type="checkbox"
          checked={r.enabled}
          onChange={(e) => onToggle(e.target.checked)}
          aria-label={`${r.title} on`}
        />
        <span className="rh-switch-track" aria-hidden />
      </label>
      <button type="button" className="rh-icon" onClick={onRun} aria-label={`Run ${r.title} now`} title="Run now">
        ▶
      </button>
      <button type="button" className="rh-icon" onClick={onDelete} aria-label={`Retire ${r.title}`} title="Retire">
        ×
      </button>
    </li>
  )
}

const ACTIVITY_ICON: Record<HeadActivity['kind'], string> = {
  child: '🌱',
  routine: '⏰',
  heartbeat: '✦',
  autopilot: '🌿',
  note: '💬',
  messenger: '📨',
  system: '·',
}

function ActivityRow({ a, onOpenSession }: { a: HeadActivity; onOpenSession: (sid: string) => void }) {
  const body = (
    <>
      <span className="rh-tl-ico" aria-hidden>
        {ACTIVITY_ICON[a.kind] || '·'}
      </span>
      <span className="rh-tl-text">
        <span className="rh-tl-title">
          {a.title}
          {a.state && needsOwner(a.state) ? <span className="rh-tl-flag">needs you</span> : null}
        </span>
        {a.detail ? <span className="rh-tl-detail">{a.detail}</span> : null}
      </span>
      <time className="rh-tl-when" dateTime={a.ts}>
        {relativeWhen(a.ts)}
      </time>
    </>
  )
  return (
    <li className={`rh-tl k-${a.kind}`}>
      {a.session_id ? (
        <button type="button" className="rh-tl-row" onClick={() => onOpenSession(a.session_id!)}>
          {body}
        </button>
      ) : (
        <div className="rh-tl-row">{body}</div>
      )}
    </li>
  )
}

function HeadSettings({
  overview,
  onDone,
  run,
}: {
  overview: HeadOverview
  onDone: () => void
  run: (fn: () => Promise<unknown>, ok?: string) => Promise<void>
}) {
  const hb = overview.heartbeat
  const [every, setEvery] = useState(String(Math.max(15, hb.every_minutes || 60)))
  const [qs, setQs] = useState(hb.quiet_start || '')
  const [qe, setQe] = useState(hb.quiet_end || '')
  const owner = overview.channels.owner
  const recent = overview.channels.recent || []
  const cap = overview.capacity
  return (
    <div className="rh-settings" role="group" aria-label="Always-on settings">
      <div className="rh-setrow">
        <span>Look around every</span>
        <select value={every} onChange={(e) => setEvery(e.target.value)} aria-label="How often">
          {[15, 30, 60, 120, 240].map((m) => (
            <option key={m} value={m}>
              {m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? 's' : ''}`}
            </option>
          ))}
        </select>
      </div>
      <div className="rh-setrow">
        <span>Quiet hours</span>
        <input type="time" value={qs} onChange={(e) => setQs(e.target.value)} aria-label="Quiet from" />
        <span>to</span>
        <input type="time" value={qe} onChange={(e) => setQe(e.target.value)} aria-label="Quiet until" />
      </div>
      <div className="rh-setrow">
        <span>Send updates to</span>
        <select
          value={owner ? `${owner.channel}|${owner.chat_id}` : ''}
          onChange={(e) => {
            const v = e.target.value
            const pick = recent.find((c) => `${c.channel}|${c.chat_id}` === v) || null
            void run(() => setOwnerChannel(pick), pick ? 'Updates will reach you there too.' : 'Updates stay in Grove.')
          }}
          aria-label="Send updates to"
        >
          <option value="">Grove only</option>
          {recent.map((c) => (
            <option key={`${c.channel}|${c.chat_id}`} value={`${c.channel}|${c.chat_id}`}>
              {c.channel} · {c.username || c.chat_id}
            </option>
          ))}
        </select>
      </div>
      {recent.length === 0 && (
        <p className="rh-fine">
          To get updates on your phone, message Remedy once from Telegram, Discord or another connected chat, then pick
          it here.
        </p>
      )}
      <p className="rh-fine">
        This PC: {overview.capacity_note}
        {cap.memory_known ? ` ${cap.free_mem_pct}% memory free.` : ''}
      </p>
      <div className="rh-formrow end">
        <button type="button" className="rh-link" onClick={onDone}>
          Close
        </button>
        <button
          type="button"
          className="rh-btn"
          onClick={() =>
            void run(
              () =>
                setHeartbeat({
                  every_minutes: Number(every),
                  quiet_start: qs,
                  quiet_end: qe,
                }),
              'Saved.',
            ).then(onDone)
          }
        >
          Save
        </button>
      </div>
    </div>
  )
}

type Choice = 'hourly' | 'every' | 'daily' | 'weekdays' | 'weekly' | 'once'
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function RoutineDialog({
  name,
  canPhone,
  onClose,
  onCreate,
}: {
  name: string
  canPhone: boolean
  onClose: () => void
  onCreate: (body: Parameters<typeof createRoutine>[0]) => Promise<void>
}) {
  const [what, setWhat] = useState('')
  const [title, setTitle] = useState('')
  const [mode, setMode] = useState<'remind' | 'task'>('task')
  const [choice, setChoice] = useState<Choice>('weekdays')
  const [at, setAt] = useState('08:00')
  const [everyHours, setEveryHours] = useState(2)
  const [days, setDays] = useState<string[]>(['mon'])
  const [onceLocal, setOnceLocal] = useState('')
  const [phone, setPhone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const needsTime = choice === 'daily' || choice === 'weekdays' || choice === 'weekly'
  return (
    <div className="rh-modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        className="rh-modal"
        role="dialog"
        aria-modal="true"
        aria-label="New routine"
        onSubmit={(e) => {
          e.preventDefault()
          if (!what.trim() || busy) return
          setBusy(true)
          setErr('')
          onCreate({
            title: title.trim() || undefined,
            what: what.trim(),
            mode,
            schedule: scheduleFromChoice(choice, {
              at,
              everyHours,
              days,
              onceLocal,
            }),
            deliver: phone ? 'both' : 'grove',
          })
            .catch((x) => setErr(x instanceof Error ? x.message : 'Could not save that routine.'))
            .finally(() => setBusy(false))
        }}
      >
        <h2>New routine</h2>
        <div className="rh-seg" role="radiogroup" aria-label="Kind">
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'task'}
            className={mode === 'task' ? 'on' : ''}
            onClick={() => setMode('task')}
          >
            🔁 {name} does it
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'remind'}
            className={mode === 'remind' ? 'on' : ''}
            onClick={() => setMode('remind')}
          >
            ⏰ Just remind me
          </button>
        </div>
        <textarea
          autoFocus
          rows={3}
          value={what}
          onChange={(e) => setWhat(e.target.value)}
          placeholder={
            mode === 'task'
              ? 'e.g. Check my inbox and calendar, then give me a short brief for the day'
              : 'e.g. Take the bins out'
          }
          aria-label={mode === 'task' ? 'What should Remedy do' : 'Reminder text'}
        />
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Name (optional)"
          maxLength={120}
          aria-label="Name"
        />
        <div className="rh-setrow wrap">
          <span>When</span>
          <select value={choice} onChange={(e) => setChoice(e.target.value as Choice)} aria-label="When">
            <option value="weekdays">Weekdays</option>
            <option value="daily">Every day</option>
            <option value="weekly">Certain days</option>
            <option value="hourly">Every hour</option>
            <option value="every">Every few hours</option>
            <option value="once">Once</option>
          </select>
          {needsTime && <input type="time" value={at} onChange={(e) => setAt(e.target.value)} aria-label="At" />}
          {choice === 'every' && (
            <select value={everyHours} onChange={(e) => setEveryHours(Number(e.target.value))} aria-label="Hours">
              {[2, 3, 4, 6, 8, 12].map((h) => (
                <option key={h} value={h}>
                  every {h} hours
                </option>
              ))}
            </select>
          )}
          {choice === 'once' && (
            <input
              type="datetime-local"
              value={onceLocal}
              onChange={(e) => setOnceLocal(e.target.value)}
              aria-label="Date and time"
            />
          )}
        </div>
        {choice === 'weekly' && (
          <div className="rh-days" role="group" aria-label="Days">
            {DAYS.map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={days.includes(d)}
                className={days.includes(d) ? 'on' : ''}
                onClick={() => setDays((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]))}
              >
                {d[0].toUpperCase() + d.slice(1)}
              </button>
            ))}
          </div>
        )}
        {canPhone && (
          <label className="rh-check">
            <input type="checkbox" checked={phone} onChange={(e) => setPhone(e.target.checked)} /> Also send it to my
            phone chat
          </label>
        )}
        <p className="rh-fine">
          {mode === 'remind'
            ? 'Reminders run on your PC and use no AI.'
            : `${name} does this in the background when your PC has room, and stops to ask before paying, sending or deleting anything.`}
        </p>
        {err && (
          <div className="rh-toast err" role="alert">
            {err}
          </div>
        )}
        <div className="rh-formrow end">
          <button type="button" className="rh-link" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="rh-btn" disabled={!what.trim() || busy}>
            Save routine
          </button>
        </div>
      </form>
    </div>
  )
}
