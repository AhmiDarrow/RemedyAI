import { describe, expect, it } from 'vitest'
import {
  applyLiveToolCall,
  applyLiveToolResult,
  isFullProcessMode,
  normalizeToolProcess,
  pairToolResults,
  processDefaultCollapsed,
  showsAdvancedDiagnostics,
  showsProcessTrace,
  stepsFromMessageTools,
  TOOL_PROCESS_CYCLE,
  type LiveToolChip,
  type ProcessStep,
} from './toolLabels'

describe('tool process modes', () => {
  it('normalizes aliases (full+ → full)', () => {
    expect(normalizeToolProcess('med')).toBe('medium')
    expect(normalizeToolProcess('fullplus')).toBe('full')
    expect(normalizeToolProcess('full+')).toBe('full')
    expect(normalizeToolProcess(true)).toBe('full')
    expect(normalizeToolProcess('min')).toBe('off')
    expect(normalizeToolProcess('off')).toBe('off')
  })

  it('full reveals everything', () => {
    expect(isFullProcessMode('full')).toBe(true)
    expect(isFullProcessMode('full+')).toBe(true) // legacy
    expect(isFullProcessMode('medium')).toBe(false)
    expect(processDefaultCollapsed('full')).toBe(false)
    expect(processDefaultCollapsed('medium')).toBe(true)
    expect(processDefaultCollapsed('off', true)).toBe(false)
  })

  it('process trail always on; advanced diagnostics removed', () => {
    expect(showsProcessTrace('off')).toBe(true)
    expect(showsProcessTrace('medium')).toBe(true)
    expect(showsProcessTrace('full')).toBe(true)
    expect(showsAdvancedDiagnostics('full+')).toBe(false)
    expect(showsAdvancedDiagnostics('full')).toBe(false)
  })

  it('cycles Min → Med → Full', () => {
    expect(TOOL_PROCESS_CYCLE).toEqual(['off', 'medium', 'full'])
  })
})

describe('live tool correlation', () => {
  const start = (calls: { name: string; callId?: string }[]) => {
    let steps: ProcessStep[] = []
    let tools: LiveToolChip[] = []
    for (const c of calls) {
      const out = applyLiveToolCall(steps, tools, c, 1000)
      steps = out.steps
      tools = out.tools
    }
    return { steps, tools }
  }

  it('matches three parallel reads by id when results arrive out of order', () => {
    let { steps, tools } = start([
      { name: 'file_read', callId: 'c1' },
      { name: 'file_read', callId: 'c2' },
      { name: 'file_read', callId: 'c3' },
    ])
    for (const r of [
      { name: 'file_read', callId: 'c3', preview: 'C' },
      { name: 'file_read', callId: 'c1', preview: 'A' },
      { name: 'file_read', callId: 'c2', preview: 'B', ok: false },
    ]) {
      const out = applyLiveToolResult(steps, tools, r, 2000)
      expect(out.matched).toBe(true)
      steps = out.steps
      tools = out.tools
    }
    expect(steps).toHaveLength(3)
    expect(steps.map((s) => s.resultText)).toEqual(['A', 'B', 'C'])
    expect(steps.map((s) => s.status)).toEqual(['done', 'error', 'done'])
    expect(tools.map((t) => t.status)).toEqual(['done', 'error', 'done'])
  })

  it('falls back to name + order when frames carry no id', () => {
    const s0 = start([{ name: 'file_read' }, { name: 'bash_exec' }, { name: 'file_read' }])
    const s1 = applyLiveToolResult(s0.steps, s0.tools, { name: 'file_read', preview: 'first' }, 2000)
    const s2 = applyLiveToolResult(s1.steps, s1.tools, { name: 'file_read', preview: 'second' }, 2001)
    expect(s2.steps[0].resultText).toBe('first')
    expect(s2.steps[2].resultText).toBe('second')
    expect(s2.steps[1].status).toBe('running')
    expect(s2.tools[1].status).toBe('running')
  })

  it('an id-less result never blocks a later id frame from its own step', () => {
    const s0 = start([
      { name: 'file_read', callId: 'c1' },
      { name: 'file_read', callId: 'c2' },
    ])
    // Legacy result without id lands on the first running read.
    const s1 = applyLiveToolResult(s0.steps, s0.tools, { name: 'file_read', preview: 'x' }, 2000)
    // Then c2 arrives with its id — still resolves onto its own step.
    const out = applyLiveToolResult(
      s1.steps,
      s1.tools,
      { name: 'file_read', callId: 'c2', preview: 'y' },
      2001,
    )
    expect(out.matched).toBe(true)
    expect(out.steps.map((s) => s.resultText)).toEqual(['x', 'y'])
  })

  it('ignores a duplicated tool_call frame for the same id', () => {
    const a = applyLiveToolCall([], [], { name: 'web_search', callId: 'w1' }, 1000)
    const b = applyLiveToolCall(a.steps, a.tools, { name: 'web_search', callId: 'w1' }, 1001)
    expect(b.steps).toHaveLength(1)
    expect(b.tools).toHaveLength(1)
  })

  it('records an orphan result when no call frame was seen', () => {
    const out = applyLiveToolResult(
      [],
      [],
      { name: 'memory_add', callId: 'm1', preview: 'saved' },
      1000,
    )
    expect(out.matched).toBe(false)
    expect(out.steps).toHaveLength(1)
    expect(out.steps[0]).toMatchObject({ callId: 'm1', status: 'done', resultText: 'saved' })
  })
})

describe('history tool pairing', () => {
  it('pairs persisted results by id regardless of order', () => {
    const calls = [
      { name: 'file_read', id: 'c1', args: { path: 'a' } },
      { name: 'file_read', id: 'c2', args: { path: 'b' } },
      { name: 'file_read', id: 'c3', args: { path: 'c' } },
    ]
    const results = [
      { name: 'file_read', id: 'c3', output: 'C' },
      { name: 'file_read', id: 'c1', output: 'A' },
      { name: 'file_read', id: 'c2', output: 'B', error: 'boom' },
    ]
    const steps = stepsFromMessageTools(calls, results)
    expect(steps.map((s) => s.resultText)).toEqual(['A', 'B', 'C'])
    expect(steps.map((s) => s.status)).toEqual(['done', 'error', 'done'])
    expect(steps.map((s) => s.callId)).toEqual(['c1', 'c2', 'c3'])
  })

  it('keeps name + order pairing for rows without ids', () => {
    const paired = pairToolResults(
      [{ name: 'file_read' }, { name: 'bash_exec' }],
      [
        { name: 'file_read', output: 'r' },
        { name: 'bash_exec', output: 'b' },
      ],
    )
    expect(paired.map((r) => r?.output)).toEqual(['r', 'b'])
  })

  it('does not hand an id-owned result to an id-less call', () => {
    const paired = pairToolResults(
      [{ name: 'file_read' }, { name: 'file_read', id: 'c2' }],
      [{ name: 'file_read', id: 'c2', output: 'owned' }],
    )
    expect(paired[0]).toBeUndefined()
    expect(paired[1]?.output).toBe('owned')
  })
})

describe('turn identity on live steps', () => {
  it('stamps the turn id so the trail can fetch the recorded result', () => {
    const call = applyLiveToolCall(
      [],
      [],
      { name: 'bash', callId: 'c1', requestId: 'req-3', args: { command: 'ls' } },
      1000,
    )
    expect(call.steps[0]?.requestId).toBe('req-3')
    const res = applyLiveToolResult(
      call.steps,
      call.tools,
      { name: 'bash', callId: 'c1', preview: 'a b', ok: true, requestId: 'req-3' },
      2000,
    )
    expect(res.matched).toBe(true)
    expect(res.steps[0]?.requestId).toBe('req-3')
    expect(res.steps[0]?.callId).toBe('c1')
  })

  it('stamps a result-only step too, so an orphan is still addressable', () => {
    const out = applyLiveToolResult(
      [],
      [],
      { name: 'screenshot', callId: 'c9', preview: '[image]', requestId: 'req-4' },
      2000,
    )
    expect(out.matched).toBe(false)
    expect(out.steps[0]?.requestId).toBe('req-4')
    expect(out.steps[0]?.callId).toBe('c9')
  })
})
