/** Human labels for built-in tools — language-agnostic icons pair with these. */

export type ToolProcessMode = 'off' | 'medium' | 'full'

export const TOOL_PROCESS_MODES: { id: ToolProcessMode; label: string; hint: string }[] = [
  { id: 'off', label: 'Off', hint: 'Minimal — progress only' },
  { id: 'medium', label: 'Medium', hint: 'Labels + status + short results' },
  { id: 'full', label: 'Full', hint: 'Complete raw args + every tool stdout/result' },
]

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
  if (s === 'full' || s === 'on' || s === 'true' || s === '1') return 'full'
  // legacy show_tool_calls true
  if (raw === true) return 'full'
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
