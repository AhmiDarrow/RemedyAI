import {
  useState,
  useEffect,
  useRef,
  useId,
  type ComponentType,
  type CSSProperties,
} from 'react'
import { skillDeleteConfirm } from '../utils/confirmMessages'
import { ConfirmDialog } from './ConfirmDialog'
import { SkillsLibrary } from './SkillsLibrary'
import { Panel } from './Panel'
import { useAsyncScope } from '../hooks/useAsyncScope'
import { apiFetch } from '../api/client'
import { getLatestCheckpoint, getLatestPlan, getLifeBoard, createLifeGoal, patchLifeGoal } from '../api/partner'
import { approvePlan } from '../api/plans'
import { isTauri, tauriInvoke } from '../api/tauri'

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
  const [facts, setFacts] = useState<{ text: string; category: string }[]>([])
  const [memoryQuery, setMemoryQuery] = useState('')
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

  const [error, setError] = useState<string | null>(null)
  const scope = useAsyncScope(open ? sessionId ?? '__global__' : null)
  const actionBusy = useRef(false)
  const viewId = useId()

  const load = async () => {
    const current = scope.latest()
    setLoading(true)
    setError(null)
    const [notes, factList, progress, board, latestPlan] = await Promise.allSettled([
      apiFetch<{ results?: { id: string; title: string; content: string; type: string }[] }>(
        `/memory/search?query=${encodeURIComponent(memoryQuery)}&limit=20`,
      ),
      apiFetch<{ facts?: { text: string; category: string }[] }>('/memory/facts?limit=12'),
      getLatestCheckpoint(sessionId),
      getLifeBoard(),
      getLatestPlan(sessionId),
    ])
    if (!current()) return
    setEntries(notes.status === 'fulfilled' ? notes.value.results || [] : [])
    setFacts(factList.status === 'fulfilled' ? factList.value.facts || [] : [])
    setCheckpoint(progress.status === 'fulfilled' ? progress.value.checkpoint : null)
    setCheckpointMd(progress.status === 'fulfilled' ? progress.value.markdown || null : null)
    const life = board.status === 'fulfilled' ? board.value : null
    setLifeGoals(life?.goals.filter((goal) => !['done', 'dropped'].includes(String(goal.status || 'open'))) || [])
    setLifeFolder(life?.life_folder || null)
    setLastStep(life?.last_step || null)
    setLifeDigest(life?.digest || null)
    setPlan(latestPlan.status === 'fulfilled' && latestPlan.value.plan
      ? { ...latestPlan.value.plan, markdown: latestPlan.value.markdown } : null)
    const failures = [notes, factList, progress, board, latestPlan].filter((result) => result.status === 'rejected').length
    if (failures) setError('Some memory information could not be loaded. Check the connection and retry.')
    setLoading(false)
  }

  useEffect(() => {
    scope.latest() // Invalidate the previous query before the debounce delay.
    if (!open) return
    const timer = window.setTimeout(() => void load(), memoryQuery ? 250 : 0)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId, memoryQuery])

  useEffect(() => {
    actionBusy.current = false
    setBusy(false)
    setPlan(null)
    setCheckpoint(null)
  }, [open, sessionId])

  const runAction = async (operation: () => Promise<unknown>, after?: () => void) => {
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(true)
    setError(null)
    try {
      await operation()
      if (!current()) return
      after?.()
      await load()
    } catch (cause: unknown) {
      if (current()) setError(cause instanceof Error ? cause.message : 'Could not save this change. Please retry.')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(false) }
    }
  }

  const approve = () => {
    if (plan?.id) void runAction(() => approvePlan(plan.id))
  }

  const tabBtn = (id: typeof tab, label: string) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      role="tab"
      id={`${viewId}-${id}`}
      aria-selected={tab === id}
      aria-controls={`${viewId}-content`}
      className="flex-1 text-[11px] py-1.5 rounded"
      style={{
        background: tab === id ? 'var(--accent)' : 'transparent',
        color: tab === id ? 'var(--accent-foreground)' : 'var(--text-primary)',
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
      {error && <div role="alert" className="mb-3 text-xs" style={{ color: 'var(--error)' }}>
        {error} <button type="button" className="underline" onClick={() => void load()}>Retry</button>
      </div>}
      {tab === 'memory' && <div className="mb-3">          <input
            type="search"
            value={memoryQuery}
            onChange={(e) => setMemoryQuery(e.target.value)}
            placeholder="Search notes"
            aria-label="Search notes"
            className="w-full text-[12px] px-2 py-1.5 rounded"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          />
</div>}
      <div role="tabpanel" id={`${viewId}-content`} aria-labelledby={`${viewId}-${tab}`}>
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading…</div>
      ) : tab === 'memory' ? (
        <div className="space-y-2">
          {facts.length > 0 ? (
            <div>
              <div
                className="text-[10px] uppercase tracking-wide mb-1"
                style={{ color: 'var(--text-muted)' }}
              >
                Partner facts
              </div>
              {facts.map((f) => (
                <div
                  key={f.text}
                  className="mb-1.5 p-2 rounded text-[12px]"
                  style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
                >
                  {f.text}
                </div>
              ))}
            </div>
          ) : null}
        {entries.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
            {memoryQuery.trim()
              ? `No notes match “${memoryQuery.trim()}”. Try a different search.`
              : 'No notes yet. When Remedy saves progress, recent notes appear here.'}
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
        )}
        </div>
      ) : tab === 'life' ? (
        <div className="space-y-2">
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', lineHeight: 1.45 }}>
            What you want to finish — held across chats. One next move at a time. Notes land
            in a folder you can open; say <strong>I did it</strong> when you finish a move, or{' '}
            <strong>I&apos;m back</strong> to hear what Remedy already did.
          </div>
          {lifeFolder && isTauri() ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                void tauriInvoke('open_path', { path: lifeFolder }).catch((cause: unknown) =>
                  setError(cause instanceof Error ? cause.message : 'Could not open the Life folder.'))
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
              if (!title) return
              void runAction(() => createLifeGoal(title), () => setNewGoal(''))
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
                    void runAction(() => patchLifeGoal(g.id, { next_action: action }),
                      () => setNextDraft((draft) => ({ ...draft, [g.id]: '' })))
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
                      void runAction(() => patchLifeGoal(g.id, { status: 'done', evidence: 'marked done in Life tab' }))
                    }}
                  >
                    Done
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy}
                    onClick={() => {
                      void runAction(() => patchLifeGoal(g.id, { status: 'paused' }))
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
          No plan yet. Switch to Plan mode (Shift+Tab) and ask Remedy to outline steps, or type{' '}
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
      </div>
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
  const scope = useAsyncScope(open ? 'skills' : null)
  const actionBusy = useRef(false)
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
  // Remedy's own confirm for skill deletion (never the browser popup).
  const [confirmSkill, setConfirmSkill] = useState<string | null>(null)

  const [reuse, setReuse] = useState<{
    total_activations: number
    skills_with_activation: number
    multi_session_reactivations: number
  } | null>(null)

  const load = () => {
    const current = scope.latest()
    setLoading(true)
    setError(null)
    return import('../api/skills')
      .then(async ({ listSkills }) => {
        const { apiFetch } = await import('../api/client')
        const [list, summary, metrics, packs] = await Promise.all([
          listSkills(),
          apiFetch<LearningSummary>('/skills/learning/summary').catch(() => null),
          import('../api/partner')
            .then(({ getSkillReuseMetrics }) => getSkillReuseMetrics())
            .catch(() => null),
          import('../api/skills')
            .then(({ getSkillPacks }) => getSkillPacks())
            .catch(() => null),
        ])
        if (!current()) return
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
        if (!current()) return
        setSkills([])
        setLearning(null)
        setReuse(null)
        setError('Failed to load skills')
      })
      .finally(() => { if (current()) setLoading(false) })
  }

  useEffect(() => {
    actionBusy.current = false
    setBusy(null)
    setEditSaving(false)
    setConfirmSkill(null)
    if (!open) return
    void load()
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
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(name)
    setError(null)
    try {
      const { setSkillStatus } = await import('../api/skills')
      await setSkillStatus(name, 'active', { force_promote: true, quarantine: false })
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Promote failed')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(null) }
    }
  }

  const archiveSkill = async (name: string, archive: boolean) => {
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(name)
    setError(null)
    try {
      const { setSkillStatus } = await import('../api/skills')
      await setSkillStatus(name, archive ? 'archived' : 'active', {
        force_promote: !archive,
        quarantine: false,
      })
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Archive update failed')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(null) }
    }
  }

  const deleteSkillRow = (name: string) =>
    setConfirmSkill(name)

  const doDeleteSkill = async (name: string) => {
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(name)
    setError(null)
    try {
      const { deleteSkill } = await import('../api/skills')
      await deleteSkill(name)
      if (!current()) return
      setSelected((prev) => {
        const next = new Set(prev)
        next.delete(name)
        return next
      })
      if (editName === name) setEditName(null)
      setPackMsg(`Deleted ${name}`)
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(null) }
    }
  }

  const visibleSkills = skills.filter((s) => {
    const query = filter.trim().toLowerCase()
    if (query && ![s.name, s.description, ...(s.tags || [])].some((value) => value.toLowerCase().includes(query))) return false
    const st = (s.status || '').toLowerCase()
    if (statusFilter === 'all') return st !== 'archived' // hide archived from default list
    if (statusFilter === 'active') return st === 'active' && !s.quarantine
    if (statusFilter === 'learned') return Boolean(s.auto_generated)
    if (statusFilter === 'quarantine') return Boolean(s.quarantine)
    if (statusFilter === 'archived') return st === 'archived'
    return true
  })

  const toggleQuarantine = async (name: string, on: boolean) => {
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(name)
    setError(null)
    try {
      const { setSkillQuarantine } = await import('../api/skills')
      await setSkillQuarantine(name, on)
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Quarantine update failed')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(null) }
    }
  }

  const openEditor = async (name: string) => {
    if (actionBusy.current) return
    actionBusy.current = true
    const current = scope.capture()
    setBusy(name)
    setError(null)
    try {
      const { getSkillDetail } = await import('../api/skills')
      const d = await getSkillDetail(name)
      if (!current()) return
      setEditName(name)
      setEditBody(
        typeof d.body === 'string' && d.body
          ? d.body
          : (d.instructions_preview || ''),
      )
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Failed to load skill body')
    } finally {
      if (current()) { actionBusy.current = false; setBusy(null) }
    }
  }

  const saveEditor = async () => {
    const current = scope.capture()
    if (!editName || editSaving || actionBusy.current) return
    setEditSaving(true)
    setError(null)
    try {
      const { saveSkillBody } = await import('../api/skills')
      await saveSkillBody(editName, editBody)
      if (!current()) return
      setEditName(null)
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      if (current()) setEditSaving(false)
    }
  }

  const exportPack = async () => {
    const current = scope.capture()
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
      if (current()) setError(e instanceof Error ? e.message : 'Export failed')
    }
  }

  const importPack = async () => {
    const current = scope.capture()
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
      if (current()) await load()
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : 'Import failed')
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
                color: on ? 'var(--accent-foreground)' : 'var(--text-primary)',
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
                if (e.nativeEvent.isComposing) return
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
            <button type="button" onClick={() => void load()} aria-label="Refresh installed skills" className="text-[10px] px-2 py-1.5 rounded" style={btnGhost}>
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
            <div role="alert" className="mb-2 text-[11px]" style={{ color: 'var(--danger, #f66)' }}>
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
                              disabled={busy !== null || editSaving}
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
                              disabled={busy !== null || editSaving}
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
                              disabled={busy !== null || editSaving}
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
                            disabled={busy !== null || editSaving}
                            onClick={() => void archiveSkill(s.name, !isArchived)}
                            className="text-[10px] px-1.5 py-0.5 rounded"
                            style={btnGhost}
                          >
                            {isArchived ? 'Restore' : 'Archive'}
                          </button>
                          <button
                            type="button"
                            disabled={busy !== null || editSaving}
                            onClick={() => void openEditor(s.name)}
                            className="text-[10px] px-1.5 py-0.5 rounded"
                            style={{ ...btnGhost, color: 'var(--accent)' }}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            disabled={busy !== null || editSaving}
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
      <ConfirmDialog
        open={Boolean(confirmSkill)}
        {...skillDeleteConfirm(confirmSkill || '')}
        onCancel={() => setConfirmSkill(null)}
        onConfirm={() => {
          const name = confirmSkill
          setConfirmSkill(null)
          if (name) void doDeleteSkill(name)
        }}
      />
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
