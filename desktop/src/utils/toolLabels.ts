/** Human labels for built-in tools — language-agnostic icons pair with these. */

/**
 * Tool-process visibility — progressive detail in the **same** Process UI.
 *
 * - **Min (off)**: step names + status only.
 * - **Med**: path/command one-liners + short result body.
 * - **Full**: complete args/results, open by default.
 *
 * Chat answer is never truncated. (Legacy `full+` maps to Full.)
 */
export type ToolProcessMode = 'off' | 'medium' | 'full'

export const TOOL_PROCESS_MODES: { id: ToolProcessMode; label: string; hint: string }[] = [
  {
    id: 'off',
    label: 'Min',
    hint: 'Process steps as names only — answer always full',
  },
  {
    id: 'medium',
    label: 'Med',
    hint: 'Same list with path/command + short result under each step',
  },
  {
    id: 'full',
    label: 'Full',
    hint: 'Same list with complete args/results (auto-scrolls while live)',
  },
]

/** Cycle order for status-bar Proc button. */
export const TOOL_PROCESS_CYCLE: ToolProcessMode[] = ['off', 'medium', 'full']

/** Full — never truncate process dumps; expand by default. */
export function isFullProcessMode(mode: ToolProcessMode | string | undefined): boolean {
  const m = String(mode || '').toLowerCase()
  // Legacy full+ treated as full
  return m === 'full' || m === 'full+' || m === 'fullplus' || m === 'full_plus' || m === 'debug'
}

/**
 * Always render the Process trail when there are steps.
 * Depth of detail is controlled inside ProcessTrace by mode.
 */
export function showsProcessTrace(_mode?: ToolProcessMode | string): boolean {
  return true
}

/** @deprecated Full+ removed — always false (dev diagnostics not user-facing). */
export function showsAdvancedDiagnostics(_mode?: ToolProcessMode | string): boolean {
  return false
}

/** After a turn, Full starts expanded; Min/Med start collapsed. */
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
  const pretty = n
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
  return pretty || 'Using tool'
}

export function normalizeToolProcess(raw: unknown): ToolProcessMode {
  const s = String(raw ?? 'off').trim().toLowerCase()
  if (s === 'medium' || s === 'med') return 'medium'
  // full+ / debug → full (removed user-facing Full+)
  if (
    s === 'full'
    || s === 'full+'
    || s === 'fullplus'
    || s === 'full_plus'
    || s === 'debug'
    || s === 'on'
    || s === 'true'
    || s === '1'
    || s === 'yes'
  ) {
    return 'full'
  }
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
  argsText?: string
  resultText?: string
  error?: string
  /** Provider tool_call id when available (matches parallel same-name tools). */
  callId?: string
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
