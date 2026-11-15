import { toolLabel, type ProcessStep } from './toolLabels'

/**
 * Helpers for the compact Working strip: short tool names, a one-glance
 * target per step, steps grouped under the narration that introduced them,
 * and the single live status line.
 */

const SHORT: Record<string, string> = {
  bash: 'Bash',
  bash_exec: 'Bash',
  shell: 'Bash',
  run_command: 'Bash',
  powershell: 'Bash',
  file_read: 'Read',
  read_file: 'Read',
  read: 'Read',
  file_write: 'Write',
  write_file: 'Write',
  write: 'Write',
  file_edit: 'Edit',
  edit_file: 'Edit',
  edit: 'Edit',
  apply_patch: 'Edit',
  grep: 'Grep',
  search_files: 'Grep',
  glob: 'Glob',
  list_dir: 'List',
  web_search: 'Search',
  web_fetch: 'Fetch',
  memory_search: 'Memory',
  memory_add: 'Memory',
}

export function toolShortName(name: string): string {
  const key = (name || '').trim().toLowerCase()
  return SHORT[key] || toolLabel(name)
}

const TARGET_KEYS = [
  'command',
  'cmd',
  'path',
  'file_path',
  'file',
  'pattern',
  'query',
  'url',
  'glob',
  'dir',
  'directory',
]

function parseArgs(text?: string): Record<string, unknown> | null {
  if (!text) return null
  try {
    const v = JSON.parse(text)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

const PATHISH = new Set(['path', 'file_path', 'file', 'dir', 'directory'])

/** Last two path segments: enough to recognise a file without the noise. */
export function shortPath(p: string): string {
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]+/).filter(Boolean)
  if (parts.length <= 2) return p
  return `…/${parts.slice(-2).join('/')}`
}

export function clip(s: string, max = 64): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max - 1)}…` : one
}

/** One short, human-scannable target for a step (command, file, pattern…). */
export function stepTarget(step: Pick<ProcessStep, 'argsText'>, max = 64): string {
  const args = parseArgs(step.argsText)
  if (!args) return ''
  // Search tools: the pattern is the point; the folder is context.
  const pat = typeof args.pattern === 'string' ? args.pattern.trim() : ''
  const where = typeof args.path === 'string' ? args.path.trim() : ''
  if (pat && where) return clip(`${pat} in ${shortPath(where)}`, max)
  for (const k of TARGET_KEYS) {
    const v = args[k]
    if (typeof v === 'string' && v.trim()) {
      return clip(PATHISH.has(k) ? shortPath(v.trim()) : v, max)
    }
  }
  return ''
}

/** First line of an error, without a leading "Error:". */
export function shortError(err?: string, max = 80): string {
  if (!err) return ''
  const first = err.split(/\r?\n/).find((l) => l.trim()) || ''
  return clip(first.replace(/^error:\s*/i, ''), max)
}

export interface StepGroup {
  /** Narration that introduced these steps; empty for leading silent steps. */
  narration: string
  steps: ProcessStep[]
}

/** Group steps under the narration line that preceded them. */
export function groupSteps(steps: readonly ProcessStep[]): StepGroup[] {
  const groups: StepGroup[] = []
  for (const s of steps) {
    const n = s.narration?.trim()
    if (n || !groups.length) groups.push({ narration: n || '', steps: [s] })
    else groups[groups.length - 1]!.steps.push(s)
  }
  return groups
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export interface StripProgress {
  label?: string
  step?: number | null
  total?: number | null
  percent?: number | null
}

/** Percent 0–100 from explicit percent or step/total; null when unknown. */
export function progressPercent(p?: StripProgress | null): number | null {
  if (!p) return null
  if (p.percent != null && Number.isFinite(p.percent)) return Math.max(0, Math.min(100, p.percent))
  if (p.step != null && p.total) return Math.max(0, Math.min(100, (p.step / p.total) * 100))
  return null
}

/**
 * The single live status line: what is happening right now.
 * Running tool → its name + target; else the latest narration; else the
 * progress label; else thinking.
 */
export function liveStatusLine(
  steps: readonly ProcessStep[],
  opts: { progress?: StripProgress | null; thinking?: boolean } = {},
): { tool?: string; text: string } {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i]!
    if (s.status === 'running') return { tool: toolShortName(s.name), text: stepTarget(s) || s.label }
  }
  const last = steps[steps.length - 1]
  if (opts.progress?.label) {
    const p = opts.progress
    const count = p.step != null && p.total ? ` ${p.step}/${p.total}` : ''
    return { text: `${p.label}${count}` }
  }
  if (last) {
    return { tool: toolShortName(last.name), text: stepTarget(last) || last.label }
  }
  return { text: opts.thinking ? 'Thinking…' : 'Starting…' }
}

/** Elapsed span of a step list (first start → last end/now). */
export function stepsElapsed(steps: readonly ProcessStep[], now: number): number | null {
  if (!steps.length) return null
  const start = Math.min(...steps.map((s) => s.startedAt))
  const running = steps.some((s) => s.status === 'running')
  const end = running ? now : Math.max(...steps.map((s) => s.endedAt ?? s.startedAt))
  return Math.max(0, end - start)
}
