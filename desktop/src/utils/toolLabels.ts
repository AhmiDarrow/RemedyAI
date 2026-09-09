/** Human labels for built-in tools — language-agnostic icons pair with these. */

/**
 * Tool-process visibility — progressive detail in the **same** Process UI.
 *
 * - **Min (off)**: header counts + compact chips (grouped); progress bar without chip duplicate.
 * - **Med**: consecutive same tools grouped; human label + path one-liner; result on expand.
 * - **Full**: every step listed with complete args/results (never collapsed away).
 *
 * Chat answer is never truncated. (Legacy `full+` maps to Full.)
 */
export type ToolProcessMode = 'off' | 'medium' | 'full'

export const TOOL_PROCESS_MODES: { id: ToolProcessMode; label: string; hint: string }[] = [
  {
    id: 'off',
    label: 'Min',
    hint: 'Compact chips + counts — no duplicate progress chips',
  },
  {
    id: 'medium',
    label: 'Med',
    hint: 'Grouped runs, path/command line, expand for short result',
  },
  {
    id: 'full',
    label: 'Full',
    hint: 'Every step with full args and results (live auto-scroll)',
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
  /**
   * Turn id this step was recorded under. With `callId` it addresses
   * `GET /sessions/{id}/turns/{requestId}/tools/{callId}` — the full result
   * behind the trail's preview.
   */
  requestId?: string
}

export type HistoryToolCall = { name: string; args?: Record<string, unknown>; id?: string }
export type HistoryToolResult = { name: string; output?: string; error?: string; id?: string }

/**
 * Pair persisted tool results with their calls.
 *
 * Results carry the call `id` when the runtime recorded one; parallel tools
 * finish out of order, so id wins. Entries without ids fall back to the old
 * name+order rule, and a result whose id belongs to a *different* call is
 * never handed to a fallback match.
 */
export function pairToolResults(
  toolCalls: HistoryToolCall[],
  toolResults: HistoryToolResult[],
): (HistoryToolResult | undefined)[] {
  const byId = new Map<string, HistoryToolResult>()
  for (const r of toolResults) {
    if (r.id && !byId.has(r.id)) byId.set(r.id, r)
  }
  const callIds = new Set(toolCalls.map((c) => c.id).filter((x): x is string => Boolean(x)))
  const used = new Set<HistoryToolResult>()
  const claimable = (r: HistoryToolResult | undefined) =>
    Boolean(r) && !used.has(r!) && !(r!.id && callIds.has(r!.id))

  const paired: (HistoryToolResult | undefined)[] = toolCalls.map((tc) => {
    if (tc.id) {
      const hit = byId.get(tc.id)
      if (hit && !used.has(hit)) {
        used.add(hit)
        return hit
      }
    }
    return undefined
  })
  toolCalls.forEach((tc, i) => {
    if (paired[i]) return
    const positional = toolResults[i]
    let res: HistoryToolResult | undefined
    if (claimable(positional) && positional!.name === tc.name) {
      res = positional
    } else {
      res = toolResults.find((r) => claimable(r) && r.name === tc.name)
    }
    if (res) {
      used.add(res)
      paired[i] = res
    }
  })
  return paired
}

export function stepsFromMessageTools(
  toolCalls: HistoryToolCall[],
  toolResults: HistoryToolResult[],
): ProcessStep[] {
  const steps: ProcessStep[] = []
  const now = Date.now()
  const paired = pairToolResults(toolCalls, toolResults)
  toolCalls.forEach((tc, i) => {
    const res = paired[i]
    const argsText =
      tc.args && Object.keys(tc.args).length
        ? JSON.stringify(tc.args, null, 2)
        : undefined
    steps.push({
      id: tc.id || `hist-${i}-${tc.name}`,
      name: tc.name,
      label: toolLabel(tc.name),
      status: res?.error ? 'error' : 'done',
      startedAt: now - 1000 * (toolCalls.length - i),
      endedAt: now,
      argsText,
      resultText: res?.output,
      error: res?.error,
      callId: tc.id,
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

/** Chip shown while a tool runs; `callId` lets parallel same-name tools finish independently. */
export type LiveToolChip = {
  name: string
  status: 'running' | 'done' | 'error'
  callId?: string
}

export type LiveToolCall = {
  name: string
  args?: Record<string, unknown>
  callId?: string
  /** Turn id from `event: start` — lets the trail fetch this call's evidence. */
  requestId?: string
}

export type LiveToolResult = {
  name: string
  preview?: string
  ok?: boolean
  callId?: string
  requestId?: string
}

/**
 * Index of the step a `tool_result` frame belongs to.
 * The call id wins when the frame carries one; otherwise the first still-running
 * step with the same name (the pre-id wire format).
 */
export function matchToolResultStep(
  steps: ProcessStep[],
  name: string,
  callId?: string,
): number {
  if (callId) {
    const byId = steps.findIndex((s) => s.callId === callId || s.id === callId)
    if (byId >= 0) return byId
  }
  return steps.findIndex((s) => s.status === 'running' && s.name === name)
}

function matchToolChip(tools: LiveToolChip[], name: string, callId?: string): number {
  if (callId) {
    const byId = tools.findIndex((t) => t.callId === callId)
    if (byId >= 0) return byId
  }
  return tools.findIndex((t) => t.status === 'running' && t.name === name)
}

/** Append a running step + chip for a `tool_call` frame (idempotent per call id). */
export function applyLiveToolCall(
  steps: ProcessStep[],
  tools: LiveToolChip[],
  call: LiveToolCall,
  now = Date.now(),
): { steps: ProcessStep[]; tools: LiveToolChip[] } {
  const { name, args, callId, requestId } = call
  if (callId && steps.some((s) => s.callId === callId || s.id === callId)) {
    return { steps, tools }
  }
  const step: ProcessStep = {
    id: callId || `${name}-${now}-${Math.random().toString(36).slice(2, 7)}`,
    name,
    label: toolLabel(name),
    status: 'running',
    startedAt: now,
    callId: callId || undefined,
    requestId: requestId || undefined,
    argsText:
      args && Object.keys(args).length ? JSON.stringify(args, null, 2) : undefined,
  }
  return {
    steps: [...steps, step],
    tools: [...tools, { name, status: 'running', callId: callId || undefined }],
  }
}

/**
 * Resolve a `tool_result` frame onto its step + chip.
 * Unmatched results (result-only tools, lost call frame) become a finished step
 * so the trail still shows what ran.
 */
export function applyLiveToolResult(
  steps: ProcessStep[],
  tools: LiveToolChip[],
  result: LiveToolResult,
  now = Date.now(),
): { steps: ProcessStep[]; tools: LiveToolChip[]; matched: boolean } {
  const { name, preview, callId, requestId } = result
  const ok = result.ok !== false
  const status: ProcessStep['status'] = ok ? 'done' : 'error'
  const error = ok ? undefined : preview || 'tool failed'

  const chipIdx = matchToolChip(tools, name, callId)
  const nextTools =
    chipIdx >= 0
      ? tools.map((t, i) => (i === chipIdx ? { ...t, status } : t))
      : tools

  const stepIdx = matchToolResultStep(steps, name, callId)
  if (stepIdx >= 0) {
    const nextSteps = steps.map((s, i) =>
      i === stepIdx
        ? {
            ...s,
            status,
            endedAt: now,
            resultText: preview,
            error,
            callId: s.callId || callId,
            requestId: s.requestId || requestId,
          }
        : s,
    )
    return { steps: nextSteps, tools: nextTools, matched: true }
  }
  const orphan: ProcessStep = {
    id: callId || `${name}-done-${now}`,
    name,
    label: toolLabel(name),
    status,
    startedAt: now,
    endedAt: now,
    callId: callId || undefined,
    requestId: requestId || undefined,
    resultText: preview,
    error,
  }
  return { steps: [...steps, orphan], tools: nextTools, matched: false }
}
