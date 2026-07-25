/** Human labels for built-in tools — language-agnostic icons pair with these. */

/**
 * Tool-process visibility modes.
 *
 * Contract (always):
 * - The model's **chat answer** is never truncated or hidden by this setting.
 * - **Thinking** is always stored; only default open/closed changes with mode.
 *
 * - **off (Min)**: answer + thinking (collapsed). Progress chips only — no process dump.
 * - **medium**: labeled tool steps, short previews (expand for more).
 * - **full**: ALL model-visible process output — complete args/stdout, expanded by default.
 * - **full+**: full raw process + advanced continuity diagnostics.
 */
export type ToolProcessMode = 'off' | 'medium' | 'full' | 'full+'

export const TOOL_PROCESS_MODES: { id: ToolProcessMode; label: string; hint: string }[] = [
  {
    id: 'off',
    label: 'Min',
    hint: 'Answer always full · thinking collapsible · process chips only',
  },
  {
    id: 'medium',
    label: 'Med',
    hint: 'Answer always full · tool labels + short previews (expand for more)',
  },
  {
    id: 'full',
    label: 'Full',
    hint: 'Answer + thinking open · complete raw tool args and every result — nothing truncated',
  },
  {
    id: 'full+',
    label: 'Full+',
    hint: 'Full raw process + advanced diagnostics (session quality, continuity internals)',
  },
]

/** Cycle order for status-bar Proc button. */
export const TOOL_PROCESS_CYCLE: ToolProcessMode[] = ['off', 'medium', 'full', 'full+']

/** Full or Full+ — never truncate process dumps; expand by default. */
export function isFullProcessMode(mode: ToolProcessMode | string | undefined): boolean {
  const m = String(mode || '').toLowerCase()
  return m === 'full' || m === 'full+' || m === 'fullplus' || m === 'full_plus'
}

/** Whether to render the Process trail (not just progress chips). */
export function showsProcessTrace(mode: ToolProcessMode | string | undefined): boolean {
  const m = String(mode || 'off').toLowerCase()
  return m !== 'off' && m !== '' && m !== 'false' && m !== '0'
}

/** Advanced diagnostics (session quality / internal continuity) only in Full+. */
export function showsAdvancedDiagnostics(mode: ToolProcessMode | string | undefined): boolean {
  const m = String(mode || '').toLowerCase()
  return m === 'full+' || m === 'fullplus' || m === 'full_plus' || m === 'debug'
}

/** After a turn, Full/Full+ stay open so nothing is buried. */
export function processDefaultCollapsed(
  mode: ToolProcessMode | string | undefined,
  live = false,
): boolean {
  if (live) return false
  return !isFullProcessMode(mode)
}

const LABELS: Record<string, string> = {
  comfyui: 'Generating image',
  file_read: 'Reading file',
  file_write: 'Writing file',
  list_dir: 'Listing folder',
  bash_exec: 'Running command',
  local_discover: 'Finding on this PC',
  web_search: 'Searching the web',
  memory_search: 'Searching memory',
  memory_add: 'Saving memory',
  skill_run: 'Running skill',
}

export function toolLabel(name: string | undefined | null): string {
  const n = (name || '').trim()
  if (!n) return 'Using tool'
  const key = n.toLowerCase()
  if (LABELS[key]) return LABELS[key]
  // snake_case → Title words
  const pretty = n
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
  return pretty || 'Using tool'
}

export function normalizeToolProcess(raw: unknown): ToolProcessMode {
  const s = String(raw ?? 'off').trim().toLowerCase()
  if (s === 'medium' || s === 'med') return 'medium'
  if (s === 'full+' || s === 'fullplus' || s === 'full_plus' || s === 'debug') return 'full+'
  if (s === 'full' || s === 'on' || s === 'true' || s === '1' || s === 'yes') return 'full'
  // legacy show_tool_calls true
  if (raw === true) return 'full'
  if (s === 'min' || s === 'minimal' || s === 'off' || s === 'none') return 'off'
  return 'off'
}

export type ProcessStep = {
  id: string
  name: string
  label: string
  status: 'running' | 'done' | 'error'
  startedAt: number
  endedAt?: number
  /** Short or full dump of args */
  argsText?: string
  /** Short or full dump of result */
  resultText?: string
  error?: string
}

export function stepsFromMessageTools(
  toolCalls: { name: string; args?: Record<string, unknown> }[],
  toolResults: { name: string; output?: string; error?: string }[],
): ProcessStep[] {
  const steps: ProcessStep[] = []
  const now = Date.now()
  toolCalls.forEach((tc, i) => {
    const res = toolResults[i] || toolResults.find((r) => r.name === tc.name)
    const argsText =
      tc.args && Object.keys(tc.args).length
        ? JSON.stringify(tc.args, null, 2)
        : undefined
    steps.push({
      id: `hist-${i}-${tc.name}`,
      name: tc.name,
      label: toolLabel(tc.name),
      status: res?.error ? 'error' : 'done',
      startedAt: now - 1000 * (toolCalls.length - i),
      endedAt: now,
      argsText,
      resultText: res?.output,
      error: res?.error,
    })
  })
  // Results without matching calls
  if (!toolCalls.length && toolResults.length) {
    toolResults.forEach((r, i) => {
      steps.push({
        id: `hist-r-${i}-${r.name}`,
        name: r.name,
        label: toolLabel(r.name),
        status: r.error ? 'error' : 'done',
        startedAt: now,
        endedAt: now,
        resultText: r.output,
        error: r.error,
      })
    })
  }
  return steps
}
