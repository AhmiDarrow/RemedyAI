import { describe, expect, it } from 'vitest'
import {
  isFullProcessMode,
  normalizeToolProcess,
  processDefaultCollapsed,
  showsAdvancedDiagnostics,
  showsProcessTrace,
  TOOL_PROCESS_CYCLE,
} from './toolLabels'

describe('tool process modes', () => {
  it('normalizes aliases', () => {
    expect(normalizeToolProcess('med')).toBe('medium')
    expect(normalizeToolProcess('fullplus')).toBe('full+')
    expect(normalizeToolProcess(true)).toBe('full')
    expect(normalizeToolProcess('min')).toBe('off')
    expect(normalizeToolProcess('off')).toBe('off')
  })

  it('full modes reveal everything', () => {
    expect(isFullProcessMode('full')).toBe(true)
    expect(isFullProcessMode('full+')).toBe(true)
    expect(isFullProcessMode('medium')).toBe(false)
    expect(processDefaultCollapsed('full')).toBe(false)
    expect(processDefaultCollapsed('full+')).toBe(false)
    expect(processDefaultCollapsed('medium')).toBe(true)
    expect(processDefaultCollapsed('off', true)).toBe(false)
  })

  it('process trail visibility', () => {
    expect(showsProcessTrace('off')).toBe(false)
    expect(showsProcessTrace('medium')).toBe(true)
    expect(showsProcessTrace('full')).toBe(true)
    expect(showsAdvancedDiagnostics('full+')).toBe(true)
    expect(showsAdvancedDiagnostics('full')).toBe(false)
  })

  it('cycles Min → Med → Full → Full+', () => {
    expect(TOOL_PROCESS_CYCLE).toEqual(['off', 'medium', 'full', 'full+'])
  })
})
