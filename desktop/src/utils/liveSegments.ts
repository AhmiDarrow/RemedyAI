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

export function activityLabel(sum: ActivitySummary, working: boolean): string {
  const parts = [working ? 'Working…' : 'Worked']
  if (sum.total) parts.push(`${sum.total} step${sum.total === 1 ? '' : 's'}`)
  if (sum.errors) parts.push(`${sum.errors} error${sum.errors === 1 ? '' : 's'}`)
  return parts.join(' · ')
}
