export type BuildTodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled'

export type BuildTodo = {
  id: string
  content: string
  status: BuildTodoStatus
}

interface BuildTodosProps {
  items: BuildTodo[]
  /** True while the current turn is still streaming. */
  live?: boolean
}

function statusOf(raw: string): BuildTodoStatus {
  if (raw === 'in_progress' || raw === 'completed' || raw === 'cancelled') return raw
  return 'pending'
}

function mark(status: BuildTodoStatus): string {
  if (status === 'completed') return '✓'
  if (status === 'cancelled') return '–'
  if (status === 'in_progress') return '›'
  return ''
}

/**
 * User-facing build checklist. Items strike through as Remedy marks them done.
 */
export function BuildTodos({ items, live = false }: BuildTodosProps) {
  if (!items.length) return null
  const open = items.filter((t) => {
    const s = statusOf(t.status)
    return s === 'pending' || s === 'in_progress'
  }).length
  const done = items.filter((t) => statusOf(t.status) === 'completed').length

  return (
    <div className="px-4 py-2 flex justify-start w-full">
      <div
        className="w-full max-w-[min(var(--chat-max-width),100%)] rounded-lg px-3 py-2"
        style={{
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
        }}
        role="list"
        aria-label="Build checklist"
        aria-live="polite"
      >
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <span
            className="text-[11px] font-semibold tracking-wide uppercase"
            style={{ color: 'var(--text-muted)' }}
          >
            Build
            {live ? (
              <span className="font-medium normal-case tracking-normal"> · live</span>
            ) : null}
          </span>
          <span className="text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
            {done}/{items.length}
            {open ? ` · ${open} open` : ' · done'}
          </span>
        </div>
        <ul className="space-y-1">
          {items.map((t) => {
            const status = statusOf(t.status)
            const closed = status === 'completed' || status === 'cancelled'
            const active = status === 'in_progress'
            return (
              <li
                key={t.id}
                role="listitem"
                className="flex items-start gap-2 text-[13px] leading-snug"
                data-status={status}
              >
                <span
                  aria-hidden
                  className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
                  style={{
                    border: `1.5px solid ${
                      status === 'completed'
                        ? 'var(--accent)'
                        : active
                          ? 'var(--accent)'
                          : 'var(--border)'
                    }`,
                    background: status === 'completed' ? 'var(--accent)' : 'transparent',
                    color:
                      status === 'completed'
                        ? 'var(--bg-primary)'
                        : active
                          ? 'var(--accent)'
                          : 'var(--text-muted)',
                    boxShadow: active ? '0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent)' : undefined,
                  }}
                >
                  {mark(status)}
                </span>
                <span
                  style={{
                    color: closed ? 'var(--text-muted)' : 'var(--text-primary)',
                    textDecoration: closed ? 'line-through' : 'none',
                    textDecorationThickness: closed ? '1.5px' : undefined,
                    opacity: status === 'cancelled' ? 0.65 : 1,
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {t.content}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

export function parseTodosPayload(payload: unknown): BuildTodo[] {
  if (!payload || typeof payload !== 'object') return []
  const raw = (payload as { todos?: unknown }).todos
  if (!Array.isArray(raw)) return []
  const out: BuildTodo[] = []
  for (const row of raw) {
    if (!row || typeof row !== 'object') continue
    const r = row as Record<string, unknown>
    const content = String(r.content || r.title || '').trim()
    if (!content) continue
    out.push({
      id: String(r.id || content).slice(0, 48),
      content: content.slice(0, 240),
      status: statusOf(String(r.status || 'pending')),
    })
  }
  return out
}
