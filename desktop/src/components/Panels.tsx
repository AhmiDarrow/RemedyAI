import { useState, useEffect, useRef, type ComponentType } from 'react'

interface PanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}

/** Side panel with basic focus trap + Escape to close (a11y). */
export function Panel({ open, onClose, title, children }: PanelProps) {
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

  return (
    <div
      ref={rootRef}
      role="complementary"
      aria-label={title}
      className="flex flex-col border-l overflow-hidden fixed top-0 right-0 bottom-0 z-[80]"
      style={{
        width: 280,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
        boxShadow: '-8px 0 24px rgba(0,0,0,0.25)',
      }}
    >
      <div
        className="flex items-center justify-between px-3 py-2 border-b text-xs font-medium"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <span>{title}</span>
        <button
          ref={closeRef}
          onClick={onClose}
          className="px-1 rounded"
          style={{ color: 'var(--text-muted)' }}
          aria-label={`Close ${title}`}
        >
          {'\u00D7'}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 text-xs">
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
  const [tab, setTab] = useState<'memory' | 'checkpoint' | 'plan'>('memory')
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
      import('../api/partner').then(({ getLatestCheckpoint, getLatestPlan }) =>
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
      className="flex-1 text-[10px] py-1 rounded"
      style={{
        background: tab === id ? 'var(--bg-tertiary)' : 'transparent',
        color: tab === id ? 'var(--accent)' : 'var(--text-muted)',
        border: tab === id ? '1px solid var(--border)' : '1px solid transparent',
      }}
    >
      {label}
    </button>
  )

  return (
    <Panel open={open} onClose={onClose} title="Memory & progress">
      <div className="mb-2 flex gap-1">
        {tabBtn('memory', 'Memory')}
        {tabBtn('checkpoint', 'Checkpoint')}
        {tabBtn('plan', 'Plan')}
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
      ) : tab === 'memory' ? (
        entries.length === 0 ? (
          <div style={{ color: 'var(--text-muted)' }}>No entries</div>
        ) : (
          entries.map((e) => (
            <div
              key={e.id}
              className="mb-2 p-2 rounded"
              style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
            >
              <div className="font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                {e.title}
              </div>
              <div className="mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                {e.content.slice(0, 120)}
              </div>
              <div className="mt-1" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
                {e.type}
              </div>
            </div>
          ))
        )
      ) : tab === 'checkpoint' ? (
        !checkpoint ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
            No mid-task checkpoints yet. Long Build runs auto-save progress under
            ~/.remedy/checkpoints/.
          </div>
        ) : (
          <div className="space-y-2">
            <div
              className="p-2 rounded"
              style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
            >
              <div className="font-medium" style={{ color: 'var(--accent)' }}>
                {checkpoint.title}
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                {checkpoint.reason || 'checkpoint'}
                {checkpoint.tool_step_count != null
                  ? ` · ${checkpoint.tool_step_count} tools`
                  : ''}
              </div>
              {(checkpoint.done?.length || 0) > 0 && (
                <div className="mt-1.5">
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}>Done</div>
                  <ul className="m-0 pl-3" style={{ color: 'var(--text-primary)', fontSize: '0.7rem' }}>
                    {checkpoint.done!.slice(0, 8).map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(checkpoint.next_steps?.length || 0) > 0 && (
                <div className="mt-1.5">
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}>Next</div>
                  <ul className="m-0 pl-3" style={{ color: 'var(--text-primary)', fontSize: '0.7rem' }}>
                    {checkpoint.next_steps!.slice(0, 6).map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(checkpoint.failures?.length || 0) > 0 && (
                <div className="mt-1.5">
                  <div style={{ color: 'var(--danger, #f66)', fontSize: '0.7rem' }}>Failures</div>
                  <ul className="m-0 pl-3" style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}>
                    {checkpoint.failures!.slice(0, 4).map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            {checkpointMd && (
              <pre
                className="p-2 rounded overflow-x-auto whitespace-pre-wrap"
                style={{
                  fontSize: '0.65rem',
                  color: 'var(--text-muted)',
                  background: 'var(--bg-primary)',
                  border: '1px solid var(--border)',
                  maxHeight: 160,
                }}
              >
                {checkpointMd.slice(0, 1200)}
              </pre>
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
        <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
          No structured plan yet. Use Plan mode (Ctrl+B) and ask Remedy to save steps, or{' '}
          <code>/plan new …</code>.
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
            <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>
              status: {plan.status || 'draft'} · {plan.steps?.length || 0} steps
            </div>
            {(plan.steps?.length || 0) > 0 && (
              <ol className="mt-1.5 m-0 pl-4" style={{ fontSize: '0.7rem', color: 'var(--text-primary)' }}>
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

  const feedback = async (name: string, success: boolean) => {
    setBusy(name)
    try {
      const { skillFeedback } = await import('../api/skills')
      await skillFeedback(name, success)
      await load()
    } catch {
      /* ignore */
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel open={open} onClose={onClose} title="Skills (agent packs)">
      {onOpenHelp && (
        <button
          type="button"
          onClick={() => onOpenHelp('07-skills')}
          className="mb-2 w-full text-left text-[11px] px-2 py-1.5 rounded"
          style={{
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            color: 'var(--accent)',
          }}
        >
          Skills guide in Help wiki →
        </button>
      )}
      <div className="mb-2 flex gap-1 items-center">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load()}
          placeholder="Search skills…"
          className="flex-1 text-xs px-2 py-1 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="button"
          onClick={load}
          className="text-xs px-2 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          Search
        </button>
      </div>
      <div className="mb-2 flex flex-wrap gap-1">
        <button
          type="button"
          onClick={() => void exportPack()}
          className="text-[10px] px-2 py-1 rounded font-medium"
          style={{
            background: 'var(--accent)',
            color: '#fff',
          }}
          title={
            selected.size
              ? `Export ${selected.size} selected`
              : 'Export all skills as ZIP pack'
          }
        >
          Export Pack{selected.size ? ` (${selected.size})` : ''}
        </button>
        <button
          type="button"
          onClick={() => void importPack()}
          className="text-[10px] px-2 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          Import Pack
        </button>
        <button
          type="button"
          onClick={() => {
            void (async () => {
              setError(null)
              setPackMsg(null)
              try {
                const { archiveUnusedSkills } = await import('../api/skills')
                const dry = await archiveUnusedSkills({ days: 90, dry_run: true })
                if (!dry.count) {
                  setPackMsg('No learned skills unused for 90+ days.')
                  return
                }
                const ok = window.confirm(
                  `Archive ${dry.count} unused learned skill(s) (idle >90 days)?\n\n` +
                    dry.candidates
                      .slice(0, 12)
                      .map((c) => c.name)
                      .join(', ') +
                    (dry.candidates.length > 12 ? '…' : ''),
                )
                if (!ok) return
                const r = await archiveUnusedSkills({ days: 90, dry_run: false })
                setPackMsg(`Archived ${r.count} skill(s).`)
                await load()
              } catch (e) {
                setError(e instanceof Error ? e.message : 'Archive unused failed')
              }
            })()
          }}
          className="text-[10px] px-2 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Archive learned skills with no activity for 90 days"
        >
          Archive unused (90d)
        </button>
        {selected.size > 0 && (
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="text-[10px] px-2 py-1 rounded"
            style={{ color: 'var(--text-muted)' }}
          >
            Clear selection
          </button>
        )}
      </div>
      {packMsg && (
        <div className="mb-2 text-[10px]" style={{ color: 'var(--success, #3ecf8e)' }}>
          {packMsg}
        </div>
      )}
      {budgetBanner && (
        <div
          className="mb-2 text-[10px] px-2 py-1.5 rounded"
          style={{
            background: 'color-mix(in srgb, var(--warning, #e6a23c) 15%, var(--bg-tertiary))',
            border: '1px solid var(--warning, #e6a23c)',
            color: 'var(--text-primary)',
          }}
        >
          {budgetBanner}
        </div>
      )}
      <div className="mb-2 flex flex-wrap gap-1">
        {(
          [
            ['all', 'Active set'],
            ['active', 'Promoted'],
            ['learned', 'Learned'],
            ['quarantine', 'Quarantine'],
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
      <p className="mb-2" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
        Human overrides: force-promote, quarantine, or archive for large libraries (100+).
        Archived skills stay on disk but leave the hot catalog.
      </p>
      {learning && (
        <div
          className="mb-3 p-2 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
          }}
        >
          <div
            className="font-medium mb-1"
            style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}
          >
            What I learned
          </div>
          <div
            className="mb-1.5 flex flex-wrap gap-2"
            style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}
          >
            <span>{learning.learned_count} learned</span>
            <span>{learning.probation_count} on probation</span>
            <span>{learning.active_learned_count} promoted</span>
            {reuse && (
              <>
                <span>{reuse.total_activations} activations</span>
                <span>{reuse.multi_session_reactivations} multi-session re-use</span>
              </>
            )}
          </div>
          {learning.recent.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
              {learning.note || 'No auto-learned skills yet.'}
            </div>
          ) : (
            <ul className="m-0 p-0 list-none space-y-1">
              {learning.recent.slice(0, 5).map((s) => (
                <li
                  key={`learned-${s.name}`}
                  style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}
                >
                  <span style={{ color: 'var(--accent)' }}>{s.name}</span>
                  <span style={{ color: statusColor(s.status) }}> · {s.status || '?'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && (
        <div className="mb-2 text-xs" style={{ color: 'var(--danger, #f66)' }}>
          {error}
        </div>
      )}
      {editName && (
        <div
          className="mb-3 p-2 rounded"
          style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium" style={{ color: 'var(--accent)' }}>
              Edit {editName}
            </span>
            <button
              type="button"
              onClick={() => setEditName(null)}
              className="text-xs"
              style={{ color: 'var(--text-muted)' }}
            >
              Close
            </button>
          </div>
          <SkillMarkdownEditorLazy value={editBody} onChange={setEditBody} />
          <button
            type="button"
            disabled={editSaving}
            onClick={() => void saveEditor()}
            className="mt-2 w-full text-xs py-1.5 rounded font-medium"
            style={{ background: 'var(--accent)', color: '#fff' }}
          >
            {editSaving ? 'Saving…' : 'Save SKILL.md'}
          </button>
        </div>
      )}
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
      ) : visibleSkills.length === 0 ? (
        <div style={{ color: 'var(--text-muted)' }}>
          {skills.length === 0 ? 'No skills loaded' : 'No skills in this filter'}
        </div>
      ) : (
        visibleSkills.map((s) => (
          <div
            key={s.name}
            className="mb-2 p-2 rounded"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          >
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="checkbox"
                checked={selected.has(s.name)}
                onChange={() => toggleSelect(s.name)}
                title="Select for export pack"
                aria-label={`Select ${s.name}`}
              />
              <span className="font-medium" style={{ color: 'var(--accent)' }}>
                {s.name}
              </span>
              <span
                className="text-xs px-1.5 rounded"
                style={{
                  color: statusColor(s.status),
                  border: `1px solid ${statusColor(s.status)}`,
                  fontSize: '0.65rem',
                }}
              >
                {s.status || 'unknown'}
              </span>
              {s.quarantine && (
                <span style={{ color: 'var(--danger, #f66)', fontSize: '0.65rem' }}>
                  quarantine
                </span>
              )}
              {s.auto_generated && (
                <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>learned</span>
              )}
            </div>
            <div className="mt-0.5" style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
              {s.description}
            </div>
            <div
              className="mt-1.5 flex flex-wrap gap-1 items-center"
              style={{ fontSize: '0.7rem' }}
            >
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() =>
                  void archiveSkill(
                    s.name,
                    (s.status || '').toLowerCase() !== 'archived',
                  )
                }
                className="text-[10px] px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title={
                  (s.status || '').toLowerCase() === 'archived'
                    ? 'Restore to active set'
                    : 'Archive (leave hot catalog, keep on disk)'
                }
              >
                {(s.status || '').toLowerCase() === 'archived' ? 'Unarchive' : 'Archive'}
              </button>
              <label
                className="flex items-center gap-1 cursor-pointer"
                style={{ color: 'var(--text-secondary)' }}
                title="Force-promote to ACTIVE (skip probation)"
              >
                <input
                  type="checkbox"
                  checked={(s.status || '').toLowerCase() === 'active' && !s.quarantine}
                  disabled={busy === s.name}
                  onChange={(e) => {
                    if (e.target.checked) void forcePromote(s.name)
                    else void toggleQuarantine(s.name, false)
                  }}
                />
                Force promote
              </label>
              <label
                className="flex items-center gap-1 cursor-pointer"
                style={{ color: 'var(--text-secondary)' }}
                title="Manually quarantine (blocks script activation)"
              >
                <input
                  type="checkbox"
                  checked={Boolean(s.quarantine)}
                  disabled={busy === s.name}
                  onChange={(e) => void toggleQuarantine(s.name, e.target.checked)}
                />
                Quarantine
              </label>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => void openEditor(s.name)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--accent)' }}
              >
                Edit MD
              </button>
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => void forcePromote(s.name)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title="Force ACTIVE now"
              >
                Promote
              </button>
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => feedback(s.name, true)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                ✓
              </button>
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => feedback(s.name, false)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                ✗
              </button>
            </div>
          </div>
        ))
      )}
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
