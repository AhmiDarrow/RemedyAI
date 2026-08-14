import {
  useState,
  useEffect,
  useRef,
  type ComponentType,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { SkillsLibrary } from './SkillsLibrary'

interface PanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  /**
   * Fixed chrome under the title (tabs, etc.) — not inside the scroll body,
   * so it cannot scroll/clip above the visible viewport.
   */
  toolbar?: ReactNode
}

/** Side panel with basic focus trap + Escape to close (a11y). */
export function Panel({ open, onClose, title, children, toolbar }: PanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const prevFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    prevFocus.current = document.activeElement as HTMLElement | null
    closeRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key !== 'Tab' || !rootRef.current) return
      const focusables = rootRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      const list = [...focusables].filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null)
      if (list.length === 0) return
      const first = list[0]!
      const last = list[list.length - 1]!
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      prevFocus.current?.focus?.()
    }
  }, [open, onClose])

  // Critical: unmount when closed. A width:0 panel still contributes content height
  // in a column flex parent and was collapsing the chat feed to 0px.
  if (!open) return null

  // Sit below the in-app title bar (36px). top:0 hid the Skills title + Library
  // tabs under the window chrome so users only saw the filter list.
  const TITLEBAR_H = 36
  // Leave room for the bottom status bar so close/tabs aren't covered either.
  const STATUSBAR_H = 28

  return (
    <div
      ref={rootRef}
      role="complementary"
      aria-label={title}
      data-keep-focus
      className="flex flex-col border-l overflow-hidden fixed right-0 z-[80]"
      style={{
        background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
        borderColor: 'color-mix(in srgb, var(--border) 85%, transparent)',
        top: TITLEBAR_H,
        bottom: STATUSBAR_H,
        width: 300,
        boxShadow: '-8px 0 24px rgba(0,0,0,0.22)',
      }}
    >
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b text-xs font-semibold tracking-tight flex-shrink-0"
        style={{
          borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
          color: 'var(--text-primary)',
        }}
      >
        <span>{title}</span>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className="ui-btn ui-btn-ghost text-base leading-none"
          style={{ padding: '0.15rem 0.4rem' }}
          aria-label={`Close ${title}`}
        >
          {'\u00D7'}
        </button>
      </div>
      {toolbar != null && (
        <div
          className="flex-shrink-0 px-2 py-2 border-b"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}
        >
          {toolbar}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto p-2 text-xs">
        {children}
      </div>
    </div>
  )
}

export function MemoryPanel({
  open,
  onClose,
  sessionId,
}: {
  open: boolean
  onClose: () => void
  /** Active chat session — filters checkpoints/plans when possible */
  sessionId?: string | null
}) {
  const [tab, setTab] = useState<'memory' | 'life' | 'checkpoint' | 'plan'>('memory')
  const [lifeGoals, setLifeGoals] = useState<
    {
      id: string
      title: string
      status?: string
      horizon?: string
      next_action?: string
      next_by?: string
    }[]
  >([])
  const [newGoal, setNewGoal] = useState('')
  const [nextDraft, setNextDraft] = useState<Record<string, string>>({})
  const [lifeFolder, setLifeFolder] = useState<string | null>(null)
  const [lastStep, setLastStep] = useState<{ did?: string; path?: string } | null>(null)
  const [lifeDigest, setLifeDigest] = useState<string | null>(null)
  const [entries, setEntries] = useState<{ id: string; title: string; content: string; type: string }[]>([])
  const [checkpointMd, setCheckpointMd] = useState<string | null>(null)
  const [checkpoint, setCheckpoint] = useState<{
    id: string
    title: string
    reason?: string
    tool_step_count?: number
    done?: string[]
    next_steps?: string[]
    failures?: string[]
  } | null>(null)
  const [plan, setPlan] = useState<{
    id: string
    title: string
    status?: string
    steps?: { title: string; status?: string }[]
    markdown?: string
  } | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setLoading(true)
    void Promise.all([
      import('../api/client').then(({ apiFetch }) =>
        apiFetch<{ results?: { id: string; title: string; content: string; type: string }[] }>(
          '/memory/search?query=&limit=20',
        )
          .then((d) => setEntries(d.results || []))
          .catch(() => setEntries([])),
      ),
      import('../api/partner').then(({ getLatestCheckpoint, getLatestPlan, getLifeBoard }) =>
        Promise.all([
          getLatestCheckpoint(sessionId)
            .then((d) => {
              setCheckpoint(d.checkpoint)
              setCheckpointMd(d.markdown || null)
            })
            .catch(() => {
              setCheckpoint(null)
              setCheckpointMd(null)
            }),
          getLifeBoard()
            .then((board) => {
              setLifeGoals(
                board.goals.filter((x) => !['done', 'dropped'].includes(String(x.status || 'open'))),
              )
              setLifeFolder(board.life_folder || null)
              setLastStep(board.last_step || null)
              setLifeDigest(board.digest || null)
            })
            .catch(() => {
              setLifeGoals([])
              setLifeFolder(null)
              setLastStep(null)
              setLifeDigest(null)
            }),
          getLatestPlan(sessionId)
            .then((d) => {
              setPlan(
                d.plan
                  ? {
                      id: d.plan.id,
                      title: d.plan.title,
                      status: d.plan.status,
                      steps: d.plan.steps,
                      markdown: d.markdown,
                    }
                  : null,
              )
            })
            .catch(() => setPlan(null)),
        ]),
      ),
    ]).finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!open) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId])

  const approve = async () => {
    if (!plan?.id) return
    setBusy(true)
    try {
      const { approvePlan } = await import('../api/partner')
      const p = await approvePlan(plan.id)
      if (p) setPlan({ ...plan, status: p.status, title: p.title })
    } catch {
      /* ignore */
    } finally {
      setBusy(false)
    }
  }

  const tabBtn = (id: typeof tab, label: string) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      className="flex-1 text-[11px] py-1.5 rounded"
      style={{
        background: tab === id ? 'var(--accent)' : 'transparent',
        color: tab === id ? '#fff' : 'var(--text-primary)',
        fontWeight: tab === id ? 700 : 600,
        border: 'none',
        cursor: 'pointer',
      }}
    >
      {label}
    </button>
  )

  const memoryToolbar = (
    <div
      className="flex rounded-md p-0.5 gap-0.5"
      style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)' }}
      role="tablist"
      aria-label="Memory views"
    >
      {tabBtn('memory', 'Memory')}
      {tabBtn('life', 'Life')}
      {tabBtn('checkpoint', 'Progress')}
      {tabBtn('plan', 'Plan')}
    </div>
  )

  // Split checkpoint lines: tool failures should not sit under a cheerful "Done".
  const doneLines = checkpoint?.done || []
  const failedFromDone = doneLines.filter(looksLikeToolFailure)
  const completedLines = doneLines.filter((d) => !looksLikeToolFailure(d))
  const failedLines = [
    ...failedFromDone,
    ...(checkpoint?.failures || []).filter((f) => !failedFromDone.includes(f)),
  ]
  const nextLines = checkpoint?.next_steps || []

  return (
    <Panel open={open} onClose={onClose} title="Memory" toolbar={memoryToolbar}>
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading…</div>
      ) : tab === 'memory' ? (
        entries.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
            No notes yet. When Remedy saves progress (memory tools, goals, long tasks), recent
            notes appear here. Partner profile facts are separate and only fill in when you ask
            to remember something about yourself.
          </div>
        ) : (
          entries.map((e) => (
            <div
              key={e.id}
              className="mb-2 p-2 rounded"
              style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <div className="font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
                  {e.title}
                </div>
                {e.type ? (
                  <span
                    className="text-[9px] shrink-0 uppercase tracking-wide"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    {e.type}
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                {e.content.slice(0, 120)}
              </div>
            </div>
          ))
        )
      ) : tab === 'life' ? (
        <div className="space-y-2">
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
            What you want to finish — held across chats. One next move at a time. Notes land
            in a folder you can open; say <strong>I did it</strong> when you finish a move, or{' '}
            <strong>I&apos;m back</strong> to hear what Remedy already did.
          </div>
          {lifeFolder ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                void import('../api/tauri').then(async ({ isTauri, tauriInvoke }) => {
                  if (!isTauri()) return
                  try {
                    await tauriInvoke('open_path', { path: lifeFolder })
                  } catch {
                    /* ignore */
                  }
                })
              }}
            >
              Open Life folder
            </button>
          ) : null}
          {lastStep?.did ? (
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', lineHeight: 1.45 }}>
              Last I did: {lastStep.did}
              {lastStep.path ? (
                <span style={{ color: 'var(--text-muted)' }}>
                  {' '}
                  · {lastStep.path.replace(/\\/g, '/').split('/').pop()}
                </span>
              ) : null}
            </div>
          ) : lifeDigest ? (
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', lineHeight: 1.45 }}>
              {lifeDigest.split('\n')[0]}
            </div>
          ) : null}
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault()
              const title = newGoal.trim()
              if (!title || busy) return
              setBusy(true)
              void import('../api/partner')
                .then(({ createLifeGoal }) => createLifeGoal(title))
                .then(() => {
                  setNewGoal('')
                  load()
                })
                .finally(() => setBusy(false))
            }}
          >
            <input
              className="ui-input ui-input-sm flex-1 min-w-0"
              value={newGoal}
              placeholder="Hold a life goal…"
              aria-label="New life goal"
              onChange={(e) => setNewGoal(e.target.value)}
            />
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !newGoal.trim()}>
              Hold
            </button>
          </form>
          {lifeGoals.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
              None yet. Say what you want this year, or type it above.
            </div>
          ) : (
            lifeGoals.map((g) => (
              <div
                key={g.id}
                className="p-2 rounded space-y-1"
                style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
              >
                <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
                  {g.title}
                  <span className="ml-1 text-[9px] uppercase" style={{ color: 'var(--text-muted)' }}>
                    {g.horizon || 'season'} · {g.status || 'open'}
                  </span>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
                  Next: {g.next_action || '— none yet —'}
                  {g.next_by ? ` · ${g.next_by}` : ''}
                </div>
                <form
                  className="flex gap-1"
                  onSubmit={(e) => {
                    e.preventDefault()
                    const action = (nextDraft[g.id] || '').trim()
                    if (!action) return
                    setBusy(true)
                    void import('../api/partner')
                      .then(({ patchLifeGoal }) => patchLifeGoal(g.id, { next_action: action }))
                      .then(() => {
                        setNextDraft((d) => ({ ...d, [g.id]: '' }))
                        load()
                      })
                      .finally(() => setBusy(false))
                  }}
                >
                  <input
                    className="ui-input ui-input-sm flex-1 min-w-0"
                    value={nextDraft[g.id] || ''}
                    placeholder="Set next action…"
                    aria-label={`Next action for ${g.title}`}
                    onChange={(e) => setNextDraft((d) => ({ ...d, [g.id]: e.target.value }))}
                  />
                  <button type="submit" className="btn btn-sm" disabled={busy}>
                    Set
                  </button>
                </form>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true)
                      void import('../api/partner')
                        .then(({ patchLifeGoal }) => patchLifeGoal(g.id, { status: 'done', evidence: 'marked done in Life tab' }))
                        .then(() => load())
                        .finally(() => setBusy(false))
                    }}
                  >
                    Done
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true)
                      void import('../api/partner')
                        .then(({ patchLifeGoal }) => patchLifeGoal(g.id, { status: 'paused' }))
                        .then(() => load())
                        .finally(() => setBusy(false))
                    }}
                  >
                    Pause
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      ) : tab === 'checkpoint' ? (
        !checkpoint ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
            No progress snapshots yet. During longer Build runs, Remedy saves checkpoints so work
            can resume if a step hits a snag.
          </div>
        ) : (
          <div className="space-y-2">
            <div
              className="px-2 py-1.5 rounded text-[10px]"
              style={{
                background: 'color-mix(in srgb, var(--accent) 12%, var(--bg-tertiary))',
                border: '1px solid var(--border)',
                color: 'var(--text-secondary)',
                lineHeight: 1.4,
              }}
            >
              This is a <strong style={{ color: 'var(--text-primary)' }}>progress snapshot</strong>
              , not a crash. It records where a task left off so Remedy can continue.
            </div>
            <div
              className="p-2 rounded"
              style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
            >
              <div className="font-medium" style={{ color: 'var(--accent)' }}>
                {friendlyCheckpointTitle(checkpoint.title)}
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginTop: 2 }}>
                {humanizeCheckpointReason(checkpoint.reason)}
                {checkpoint.tool_step_count != null
                  ? ` · ${checkpoint.tool_step_count} step${
                      checkpoint.tool_step_count === 1 ? '' : 's'
                    }`
                  : ''}
              </div>

              {completedLines.length > 0 && (
                <div className="mt-2">
                  <div
                    className="font-semibold"
                    style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}
                  >
                    Completed
                  </div>
                  <ul
                    className="m-0 mt-0.5 pl-3"
                    style={{ color: 'var(--text-primary)', fontSize: '0.75rem', lineHeight: 1.4 }}
                  >
                    {completedLines.slice(0, 8).map((d, i) => (
                      <li key={i}>{humanizeCheckpointLine(d)}</li>
                    ))}
                  </ul>
                </div>
              )}

              {failedLines.length > 0 && (
                <div className="mt-2">
                  <div
                    className="font-semibold"
                    style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}
                  >
                    Hit a snag
                  </div>
                  <ul
                    className="m-0 mt-0.5 pl-3"
                    style={{ color: 'var(--text-primary)', fontSize: '0.75rem', lineHeight: 1.4 }}
                  >
                    {failedLines.slice(0, 6).map((d, i) => (
                      <li key={i}>{humanizeCheckpointLine(d)}</li>
                    ))}
                  </ul>
                </div>
              )}

              {nextLines.length > 0 && (
                <div className="mt-2">
                  <div
                    className="font-semibold"
                    style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}
                  >
                    Suggested next
                  </div>
                  <ul
                    className="m-0 mt-0.5 pl-3"
                    style={{ color: 'var(--text-primary)', fontSize: '0.75rem', lineHeight: 1.4 }}
                  >
                    {nextLines.slice(0, 6).map((d, i) => (
                      <li key={i}>{humanizeCheckpointLine(d)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {checkpointMd && (
              <details className="text-[10px]">
                <summary
                  className="cursor-pointer select-none"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Technical details
                </summary>
                <pre
                  className="mt-1 p-2 rounded overflow-x-auto whitespace-pre-wrap"
                  style={{
                    fontSize: '0.65rem',
                    color: 'var(--text-muted)',
                    background: 'var(--bg-primary)',
                    border: '1px solid var(--border)',
                    maxHeight: 140,
                    margin: 0,
                  }}
                >
                  {checkpointMd.slice(0, 1200)}
                </pre>
              </details>
            )}
            <button
              type="button"
              onClick={load}
              className="text-xs px-2 py-1 rounded w-full"
              style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            >
              Refresh
            </button>
          </div>
        )
      ) : !plan ? (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
          No plan yet. Switch to Plan mode (Ctrl+B) and ask Remedy to outline steps, or type{' '}
          <code style={{ fontSize: '0.7rem' }}>/plan new …</code>.
        </div>
      ) : (
        <div className="space-y-2">
          <div
            className="p-2 rounded"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          >
            <div className="font-medium" style={{ color: 'var(--accent)' }}>
              {plan.title}
            </div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
              {plan.status === 'approved' ? 'Approved' : plan.status === 'draft' ? 'Draft' : plan.status || 'Draft'}
              {' · '}
              {plan.steps?.length || 0} step{(plan.steps?.length || 0) === 1 ? '' : 's'}
            </div>
            {(plan.steps?.length || 0) > 0 && (
              <ol
                className="mt-1.5 m-0 pl-4"
                style={{ fontSize: '0.75rem', color: 'var(--text-primary)', lineHeight: 1.4 }}
              >
                {plan.steps!.slice(0, 12).map((s, i) => (
                  <li key={i}>
                    {s.title}
                    {s.status && s.status !== 'pending' ? ` (${s.status})` : ''}
                  </li>
                ))}
              </ol>
            )}
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              disabled={busy || plan.status === 'approved'}
              onClick={() => void approve()}
              className="flex-1 text-xs px-2 py-1 rounded"
              style={{
                border: '1px solid var(--border)',
                color: plan.status === 'approved' ? 'var(--success, #3ecf8e)' : 'var(--text-secondary)',
              }}
            >
              {plan.status === 'approved' ? 'Approved' : 'Approve plan'}
            </button>
            <button
              type="button"
              onClick={load}
              className="text-xs px-2 py-1 rounded"
              style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            >
              Refresh
            </button>
          </div>
        </div>
      )}
    </Panel>
  )
}

/** True if a checkpoint line is really a tool failure, not a success. */
function looksLikeToolFailure(line: string): boolean {
  const s = line || ''
  return (
    /error\s*\[/i.test(s)
    || /\bNOT_FOUND\b/i.test(s)
    || /\bfile not found\b/i.test(s)
    || /\bfailed\b/i.test(s)
    || /\bexception\b/i.test(s)
    || /:\s*Error\b/i.test(s)
  )
}

function humanizeCheckpointReason(reason?: string): string {
  const r = (reason || '').trim().toLowerCase()
  if (!r || r === 'checkpoint') return 'Saved progress'
  if (r === 'recovery') return 'Saved after a step had trouble'
  if (r === 'turn_end') return 'Saved at end of turn'
  if (r === 'auto' || r.startsWith('auto')) return 'Auto-saved progress'
  return r.replace(/_/g, ' ')
}

function friendlyCheckpointTitle(title?: string): string {
  const t = (title || '').trim()
  if (!t) return 'Progress snapshot'
  if (/^after tool failure$/i.test(t)) return 'Paused after a step had trouble'
  if (/tool failure/i.test(t)) return 'Paused after a step had trouble'
  return t.replace(/^#\s*Checkpoint:\s*/i, '').trim() || 'Progress snapshot'
}

/** Plain-language line for checkpoint lists (hides scary error codes by default). */
function humanizeCheckpointLine(line: string): string {
  const raw = (line || '').trim()
  if (!raw) return raw

  const notFound = raw.match(/file not found:\s*(.+?)(?:\s+Suggestion:|$)/i)
  if (notFound) {
    const file = notFound[1]!.trim().replace(/[`'"]/g, '')
    const sug = raw.match(/Suggestion:\s*(.+)/i)?.[1]?.trim()
    if (sug && /list_dir/i.test(sug)) {
      return `Couldn't find “${file}”. Next: check the folder listing.`
    }
    return `Couldn't find “${file}”.`
  }

  // Strip tool_name: Error [CODE]: prefix
  let s = raw
    .replace(/^[a-z0-9_.-]+:\s*/i, '')
    .replace(/Error\s*\[[^\]]+\]:\s*/gi, '')
    .replace(/\bSuggestion:\s*/gi, 'Tip: ')
    .replace(/\s+/g, ' ')
    .trim()

  if (s.length > 160) s = `${s.slice(0, 157)}…`
  return s
}

type SkillRow = {
  name: string
  description: string
  version: string
  status?: string
  tags?: string[]
  effort_weight?: number
  effort_band?: string | null
  auto_generated?: boolean
  quarantine?: boolean
  success_rate?: number | null
  related?: string[]
  lifecycle?: string | null
  lifecycle_last?: string | null
}

function statusColor(status?: string): string {
  switch ((status || '').toLowerCase()) {
    case 'active':
      return 'var(--success, #3ecf8e)'
    case 'validated':
      return 'var(--accent)'
    case 'discovered':
      return 'var(--warning, #e6b84d)'
    case 'disabled':
    case 'deprecated':
      return 'var(--text-muted)'
    default:
      return 'var(--text-secondary)'
  }
}

type LearningSummary = {
  recent: SkillRow[]
  probation_count: number
  learned_count: number
  active_learned_count: number
  note?: string
}

export function SkillsPanel({
  open,
  onClose,
  onOpenHelp,
}: {
  open: boolean
  onClose: () => void
  onOpenHelp?: (articleId?: string) => void
}) {
  const [panelTab, setPanelTab] = useState<'installed' | 'library'>('installed')
  const [skills, setSkills] = useState<SkillRow[]>([])
  const [learning, setLearning] = useState<LearningSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  /** all | active | learned | quarantine | archived */
  const [statusFilter, setStatusFilter] = useState<
    'all' | 'active' | 'learned' | 'quarantine' | 'archived'
  >('all')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editName, setEditName] = useState<string | null>(null)
  const [editBody, setEditBody] = useState('')
  const [editSaving, setEditSaving] = useState(false)
  const [packMsg, setPackMsg] = useState<string | null>(null)
  const [budgetBanner, setBudgetBanner] = useState<string | null>(null)

  const [reuse, setReuse] = useState<{
    total_activations: number
    skills_with_activation: number
    multi_session_reactivations: number
  } | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    void import('../api/skills')
      .then(async ({ listSkills }) => {
        const { apiFetch } = await import('../api/client')
        const [list, summary, metrics, packs] = await Promise.all([
          listSkills(filter),
          apiFetch<LearningSummary>('/skills/learning/summary').catch(() => null),
          import('../api/partner')
            .then(({ getSkillReuseMetrics }) => getSkillReuseMetrics())
            .catch(() => null),
          import('../api/skills')
            .then(({ getSkillPacks }) => getSkillPacks())
            .catch(() => null),
        ])
        setSkills(list)
        setLearning(summary)
        setBudgetBanner(packs?.budget_banner || null)
        setReuse(
          metrics
            ? {
                total_activations: metrics.total_activations,
                skills_with_activation: metrics.skills_with_activation,
                multi_session_reactivations: metrics.multi_session_reactivations,
              }
            : null,
        )
      })
      .catch(() => {
        setSkills([])
        setLearning(null)
        setReuse(null)
        setError('Failed to load skills')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!open) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const toggleSelect = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const forcePromote = async (name: string) => {
    setBusy(name)
    setError(null)
    try {
      const { setSkillStatus } = await import('../api/skills')
      await setSkillStatus(name, 'active', { force_promote: true, quarantine: false })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Promote failed')
    } finally {
      setBusy(null)
    }
  }

  const archiveSkill = async (name: string, archive: boolean) => {
    setBusy(name)
    setError(null)
    try {
      const { setSkillStatus } = await import('../api/skills')
      await setSkillStatus(name, archive ? 'archived' : 'active', {
        force_promote: !archive,
        quarantine: false,
      })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Archive update failed')
    } finally {
      setBusy(null)
    }
  }

  const deleteSkillRow = async (name: string) => {
    const ok = window.confirm(
      `Delete skill “${name}” permanently?\n\n` +
        `This removes it from the agent and deletes its folder under ~/.remedy/skills/.\n` +
        `Bundled skills cannot be deleted this way. You can reinstall library skills later.`,
    )
    if (!ok) return
    setBusy(name)
    setError(null)
    try {
      const { deleteSkill } = await import('../api/skills')
      await deleteSkill(name)
      setSelected((prev) => {
        const next = new Set(prev)
        next.delete(name)
        return next
      })
      if (editName === name) setEditName(null)
      setPackMsg(`Deleted ${name}`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBusy(null)
    }
  }

  const visibleSkills = skills.filter((s) => {
    const st = (s.status || '').toLowerCase()
    if (statusFilter === 'all') return st !== 'archived' // hide archived from default list
    if (statusFilter === 'active') return st === 'active' && !s.quarantine
    if (statusFilter === 'learned') return Boolean(s.auto_generated)
    if (statusFilter === 'quarantine') return Boolean(s.quarantine)
    if (statusFilter === 'archived') return st === 'archived'
    return true
  })

  const toggleQuarantine = async (name: string, on: boolean) => {
    setBusy(name)
    setError(null)
    try {
      const { setSkillQuarantine } = await import('../api/skills')
      await setSkillQuarantine(name, on)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Quarantine update failed')
    } finally {
      setBusy(null)
    }
  }

  const openEditor = async (name: string) => {
    setBusy(name)
    setError(null)
    try {
      const { getSkillDetail } = await import('../api/skills')
      const d = await getSkillDetail(name)
      setEditName(name)
      setEditBody(
        typeof d.body === 'string' && d.body
          ? d.body
          : (d.instructions_preview || ''),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load skill body')
    } finally {
      setBusy(null)
    }
  }

  const saveEditor = async () => {
    if (!editName) return
    setEditSaving(true)
    setError(null)
    try {
      const { saveSkillBody } = await import('../api/skills')
      await saveSkillBody(editName, editBody)
      setEditName(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setEditSaving(false)
    }
  }

  const exportPack = async () => {
    setPackMsg(null)
    setError(null)
    try {
      const { exportSkillsPack } = await import('../api/skills')
      const names = selected.size ? [...selected] : undefined
      await exportSkillsPack(names)
      setPackMsg(
        names
          ? `Exported ${names.length} skill(s) as ZIP`
          : 'Exported all skills as ZIP',
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    }
  }

  const importPack = async () => {
    setPackMsg(null)
    setError(null)
    try {
      const input = document.createElement('input')
      input.type = 'file'
      input.accept = '.zip,application/zip'
      const file = await new Promise<File | null>((resolve) => {
        input.onchange = () => resolve(input.files?.[0] ?? null)
        input.click()
      })
      if (!file) return
      const { importSkillsPack } = await import('../api/skills')
      const r = await importSkillsPack(file)
      setPackMsg(
        `Imported ${r.imported} skill(s) in quarantine: ${r.names.join(', ')}`,
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    }
  }

  const btnGhost: CSSProperties = {
    border: '1px solid var(--border)',
    color: 'var(--text-secondary)',
    background: 'transparent',
  }
  const btnAccent: CSSProperties = {
    background: 'var(--accent)',
    color: '#fff',
    border: 'none',
  }

  const skillsToolbar = (
    <div className="flex items-center gap-1.5">
      <div
        className="flex flex-1 rounded-md p-0.5 gap-0.5"
        style={{
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
        }}
        role="tablist"
        aria-label="Skills views"
      >
        {(
          [
            ['installed', 'Installed'],
            ['library', 'Library'],
          ] as const
        ).map(([id, label]) => {
          const on = panelTab === id
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={on}
              className="flex-1 text-[12px] px-2 py-2 rounded"
              style={{
                background: on ? 'var(--accent)' : 'transparent',
                color: on ? '#fff' : 'var(--text-primary)',
                fontWeight: on ? 700 : 600,
                border: 'none',
                cursor: 'pointer',
              }}
              onClick={() => setPanelTab(id)}
              title={
                id === 'library'
                  ? 'Browse and install from the signed Skills Library catalog'
                  : 'Skills already on this machine (bundled, learned, quarantined)'
              }
            >
              {label}
            </button>
          )
        })}
      </div>
      {onOpenHelp && (
        <button
          type="button"
          onClick={() => onOpenHelp('07-skills')}
          className="text-[11px] px-2 py-1.5 rounded shrink-0"
          style={{
            border: '1px solid var(--border)',
            color: 'var(--text-muted)',
          }}
          title="Skills help"
        >
          Help
        </button>
      )}
    </div>
  )

  return (
    <Panel open={open} onClose={onClose} title="Skills" toolbar={skillsToolbar}>
      {/* Keep Library mounted while Skills panel is open so list state survives
          tab flips and soft-refresh can run without remount stutter. */}
      <div
        className="flex-1 min-h-0"
        style={{
          minHeight: panelTab === 'library' ? 280 : 0,
          display: panelTab === 'library' ? 'flex' : 'none',
          flexDirection: 'column',
        }}
        hidden={panelTab !== 'library'}
      >
        <SkillsLibrary
          active={panelTab === 'library'}
          onInstalled={() => load()}
          installed={skills}
        />
      </div>

      {panelTab === 'installed' ? (
        <>
          <div className="mb-2 flex gap-1 items-center">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') {
                  e.preventDefault()
                  load()
                }
              }}
              onMouseDown={(e) => e.stopPropagation()}
              placeholder="Filter…"
              autoComplete="off"
              spellCheck={false}
              data-keep-focus
              aria-label="Filter installed skills"
              className="flex-1 text-xs px-2 py-1.5 rounded"
              style={{
                background: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
            <button type="button" onClick={load} className="text-[10px] px-2 py-1.5 rounded" style={btnGhost}>
              ↻
            </button>
          </div>

          <div className="mb-2 flex flex-wrap gap-1 items-center">
            {(
              [
                ['all', 'All'],
                ['active', 'Active'],
                ['quarantine', 'Quarantine'],
                ['learned', 'Learned'],
                ['archived', 'Archived'],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setStatusFilter(id)}
                className="text-[10px] px-2 py-0.5 rounded"
                style={{
                  background: statusFilter === id ? 'var(--accent)' : 'var(--bg-tertiary)',
                  color: statusFilter === id ? '#fff' : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="mb-2 flex flex-wrap gap-1">
            <button
              type="button"
              onClick={() => void exportPack()}
              className="text-[10px] px-2 py-0.5 rounded"
              style={btnGhost}
              title={selected.size ? `Export ${selected.size} selected` : 'Export all as ZIP'}
            >
              Export{selected.size ? ` (${selected.size})` : ''}
            </button>
            <button type="button" onClick={() => void importPack()} className="text-[10px] px-2 py-0.5 rounded" style={btnGhost}>
              Import
            </button>
            {selected.size > 0 && (
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                className="text-[10px] px-1.5 py-0.5 rounded"
                style={{ color: 'var(--text-muted)' }}
              >
                Clear
              </button>
            )}
          </div>

          {(packMsg || budgetBanner || (learning && learning.learned_count > 0)) && (
            <div className="mb-2 text-[10px] space-y-0.5" style={{ color: 'var(--text-muted)' }}>
              {packMsg && <div style={{ color: 'var(--success, #3ecf8e)' }}>{packMsg}</div>}
              {budgetBanner && <div style={{ color: 'var(--warning, #e6a23c)' }}>{budgetBanner}</div>}
              {learning && learning.learned_count > 0 && (
                <div>
                  Learned {learning.learned_count}
                  {learning.probation_count ? ` · ${learning.probation_count} probation` : ''}
                  {reuse ? ` · ${reuse.total_activations} uses` : ''}
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="mb-2 text-[11px]" style={{ color: 'var(--danger, #f66)' }}>
              {error}
            </div>
          )}

          {editName && (
            <div
              className="mb-2 p-2 rounded"
              style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium" style={{ color: 'var(--accent)' }}>
                  Edit {editName}
                </span>
                <button type="button" onClick={() => setEditName(null)} className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  Close
                </button>
              </div>
              <SkillMarkdownEditorLazy value={editBody} onChange={setEditBody} />
              <button
                type="button"
                disabled={editSaving}
                onClick={() => void saveEditor()}
                className="mt-2 w-full text-xs py-1.5 rounded font-medium"
                style={btnAccent}
              >
                {editSaving ? 'Saving…' : 'Save'}
              </button>
            </div>
          )}

          {loading ? (
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Loading…
            </div>
          ) : visibleSkills.length === 0 ? (
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              {skills.length === 0 ? 'No skills loaded' : 'Nothing in this filter'}
            </div>
          ) : (
            <div className="space-y-1.5">
              {visibleSkills.map((s) => {
                const isArchived = (s.status || '').toLowerCase() === 'archived'
                const isActive = (s.status || '').toLowerCase() === 'active' && !s.quarantine
                const desc =
                  (s.description || '').length > 100
                    ? `${(s.description || '').slice(0, 97)}…`
                    : s.description
                return (
                  <div
                    key={s.name}
                    className="px-2 py-1.5 rounded"
                    style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
                  >
                    <div className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={selected.has(s.name)}
                        onChange={() => toggleSelect(s.name)}
                        title="Select for export"
                        aria-label={`Select ${s.name}`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-xs font-medium" style={{ color: 'var(--accent)' }}>
                            {s.name}
                          </span>
                          <span
                            className="text-[9px] px-1 rounded"
                            style={{
                              color: statusColor(s.status),
                              border: `1px solid ${statusColor(s.status)}`,
                            }}
                          >
                            {s.quarantine ? 'quarantine' : s.status || '?'}
                          </span>
                          {s.auto_generated && (
                            <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
                              learned
                            </span>
                          )}
                        </div>
                        {desc && (
                          <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                            {desc}
                          </div>
                        )}
                        <div className="mt-1 flex flex-wrap gap-1">
                          {s.quarantine ? (
                            <button
                              type="button"
                              disabled={busy === s.name}
                              onClick={() => void forcePromote(s.name)}
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={btnAccent}
                              title="Trust & activate"
                            >
                              Trust
                            </button>
                          ) : !isActive && !isArchived ? (
                            <button
                              type="button"
                              disabled={busy === s.name}
                              onClick={() => void forcePromote(s.name)}
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={btnGhost}
                            >
                              Promote
                            </button>
                          ) : null}
                          {!s.quarantine && isActive && (
                            <button
                              type="button"
                              disabled={busy === s.name}
                              onClick={() => void toggleQuarantine(s.name, true)}
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={btnGhost}
                              title="Block scripts until Trust again"
                            >
                              Quarantine
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={busy === s.name}
                            onClick={() => void archiveSkill(s.name, !isArchived)}
                            className="text-[10px] px-1.5 py-0.5 rounded"
                            style={btnGhost}
                          >
                            {isArchived ? 'Restore' : 'Archive'}
                          </button>
                          <button
                            type="button"
                            disabled={busy === s.name}
                            onClick={() => void openEditor(s.name)}
                            className="text-[10px] px-1.5 py-0.5 rounded"
                            style={{ ...btnGhost, color: 'var(--accent)' }}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            disabled={busy === s.name}
                            onClick={() => void deleteSkillRow(s.name)}
                            className="text-[10px] px-1.5 py-0.5 rounded"
                            style={{
                              border: '1px solid var(--danger, #f66)',
                              color: 'var(--danger, #f66)',
                              background: 'transparent',
                            }}
                            title="Delete from disk"
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      ) : null}
    </Panel>
  )
}

/** Lazy-load CodeMirror so the panel shell stays light when unused. */
function SkillMarkdownEditorLazy({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const [Comp, setComp] = useState<null | ComponentType<{
    value: string
    onChange: (v: string) => void
    height?: string
  }>>(null)
  useEffect(() => {
    void import('./SkillMarkdownEditor').then((m) => setComp(() => m.SkillMarkdownEditor))
  }, [])
  if (!Comp) {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={12}
        className="w-full text-xs p-2 rounded font-mono"
        style={{
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
          color: 'var(--text-primary)',
        }}
      />
    )
  }
  return <Comp value={value} onChange={onChange} height="240px" />
}
