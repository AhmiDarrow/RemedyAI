/** First-class turn records (v0.32 M1.8). Stream jobs remain the paint buffer. */

export type TurnStatus =
  | 'planning'
  | 'running'
  | 'waiting'
  | 'verifying'
  | 'completed'
  | 'failed'

export type Turn = {
  sessionId: string
  turnId: string
  jobId: string
  status: TurnStatus
  goal: string
  startedAt: string
  completedAt?: string
}

export type TurnPatch = Pick<Turn, 'sessionId' | 'turnId'> & Partial<Turn>

type Listener = (turns: Turn[]) => void

const byKey = new Map<string, Turn>()
const listeners = new Set<Listener>()

function keyOf(sessionId: string, turnId: string) {
  return `${sessionId}::${turnId}`
}

function emit() {
  const all = [...byKey.values()]
  for (const fn of listeners) {
    try {
      fn(all)
    } catch {
      /* ignore */
    }
  }
}

export function upsertTurn(partial: TurnPatch): Turn {
  const k = keyOf(partial.sessionId, partial.turnId)
  const prev = byKey.get(k)
  const next: Turn = {
    sessionId: partial.sessionId,
    turnId: partial.turnId,
    jobId: '',
    status: 'running',
    goal: '',
    startedAt: new Date().toISOString(),
    ...prev,
    ...partial,
  }
  byKey.set(k, next)
  emit()
  return next
}

export function getTurn(sessionId: string, turnId: string): Turn | undefined {
  return byKey.get(keyOf(sessionId, turnId))
}

export function turnsForSession(sessionId: string): Turn[] {
  return [...byKey.values()].filter((t) => t.sessionId === sessionId)
}

export function subscribeTurns(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function clearTurnsForSession(sessionId: string) {
  for (const [k, t] of byKey) {
    if (t.sessionId === sessionId) byKey.delete(k)
  }
  emit()
}

const LIVE_STATUSES: ReadonlySet<TurnStatus> = new Set([
  'planning',
  'running',
  'waiting',
  'verifying',
])

export function isLiveTurnStatus(status: TurnStatus): boolean {
  return LIVE_STATUSES.has(status)
}

/** Plain-language line for live turns; null when completed/failed (hide). */
export function plainTurnLabel(status: TurnStatus): string | null {
  switch (status) {
    case 'planning':
    case 'running':
      return 'Working…'
    case 'waiting':
      return 'Waiting for you…'
    case 'verifying':
      return 'Checking…'
    default:
      return null
  }
}

/** Newest live turn for a session, if any. */
export function liveTurnForSession(sessionId: string): Turn | undefined {
  const live = turnsForSession(sessionId).filter((t) => isLiveTurnStatus(t.status))
  if (!live.length) return undefined
  return live.reduce((a, b) => (a.startedAt >= b.startedAt ? a : b))
}

/** Test helper — drop every turn. */
export function resetTurns() {
  byKey.clear()
  emit()
}
