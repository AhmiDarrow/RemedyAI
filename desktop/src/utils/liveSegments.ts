/**
 * Split a live turn's streamed text into interim narration and the reply.
 *
 * Breaks are offsets where a tool call started (see markJobTextBreak). Text
 * the model wrote before a tool call is narration about background work
 * ("I'll reach the folder through the shell…"); only the text after the last
 * break is the message being written to the user right now.
 */
export interface LiveSegments {
  narration: string[]
  reply: string
}

export function splitLiveText(text: string, breaks: readonly number[] = []): LiveSegments {
  const src = text || ''
  const cuts = [...breaks]
    .filter((n) => Number.isFinite(n) && n > 0)
    .map((n) => Math.min(n, src.length))
    .sort((a, b) => a - b)
  const narration: string[] = []
  let from = 0
  for (const at of cuts) {
    if (at <= from) continue
    const seg = src.slice(from, at).trim()
    if (seg) narration.push(seg)
    from = at
  }
  return { narration, reply: src.slice(from).replace(/^\s+/, '') }
}

/**
 * Narration cut by the newest break: the text between the previous break and
 * the one a tool call just added. Empty when the call added no break.
 */
export function narrationAtBreak(
  text: string,
  before: readonly number[],
  after: readonly number[],
): string {
  if (after.length <= before.length) return ''
  const end = after[after.length - 1]!
  const start = before.length ? before[before.length - 1]! : 0
  return (text || '').slice(start, end).trim()
}

/**
 * Split a saved reply into the narration recorded on its tool calls and the
 * message to the reader. Narration is removed in order (first match after the
 * previous one), so old saves without narration come back unchanged.
 */
export function splitSavedReply(
  content: string,
  toolCalls: ReadonlyArray<{ narration?: string }>,
): LiveSegments {
  const src = content || ''
  const narration: string[] = []
  let out = ''
  let cursor = 0
  for (const tc of toolCalls) {
    const n = tc.narration?.trim()
    if (!n) continue
    const at = src.indexOf(n, cursor)
    if (at < 0) continue
    out += src.slice(cursor, at)
    cursor = at + n.length
    narration.push(n)
  }
  out += src.slice(cursor)
  return { narration, reply: narration.length ? out.replace(/\n{3,}/g, '\n\n').trim() : src }
}

export interface ActivitySummary {
  total: number
  running: number
  done: number
  errors: number
}

export function summarizeSteps(
  steps: ReadonlyArray<{ status: 'running' | 'done' | 'error' }>,
): ActivitySummary {
  let running = 0
  let done = 0
  let errors = 0
  for (const s of steps) {
    if (s.status === 'running') running++
    else if (s.status === 'error') errors++
    else done++
  }
  return { total: steps.length, running, done, errors }
}
