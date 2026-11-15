import { describe, expect, it } from 'vitest'
import type { ProcessStep } from './toolLabels'
import {
  formatElapsed,
  groupSteps,
  liveStatusLine,
  progressPercent,
  shortError,
  shortPath,
  stepTarget,
  stepsElapsed,
  toolShortName,
} from './workTimeline'

const s = (id: string, extra: Partial<ProcessStep> = {}): ProcessStep => ({
  id,
  name: 'bash',
  label: 'Running command',
  status: 'done',
  startedAt: 0,
  ...extra,
})

describe('workTimeline', () => {
  it('maps tools to short names and falls back to the label', () => {
    expect(toolShortName('bash_exec')).toBe('Bash')
    expect(toolShortName('file_read')).toBe('Read')
    expect(toolShortName('file_write')).toBe('Write')
    expect(toolShortName('grep')).toBe('Grep')
    expect(toolShortName('comfyui')).toBe('Generating image')
  })

  it('picks a short target: command, short path, or pattern in folder', () => {
    expect(stepTarget({ argsText: '{"command":"git  status\\n --short"}' })).toBe('git status --short')
    expect(stepTarget({ argsText: '{"path":"C:\\\\a\\\\b\\\\c\\\\d.lua"}' })).toBe('…/c/d.lua')
    expect(stepTarget({ argsText: '{"pattern":"entry","path":"/x/y/z"}' })).toBe('entry in …/y/z')
    expect(stepTarget({ argsText: 'not json' })).toBe('')
    expect(stepTarget({ argsText: JSON.stringify({ command: 'x'.repeat(200) }) }).length).toBe(64)
    expect(shortPath('a/b')).toBe('a/b')
  })

  it('keeps only the first line of an error', () => {
    expect(shortError('Error: file not found\nstack')).toBe('file not found')
    expect(shortError(undefined)).toBe('')
  })

  it('groups steps under the narration that introduced them', () => {
    const g = groupSteps([s('0'), s('1', { narration: 'A' }), s('2'), s('3', { narration: 'B' })])
    expect(g.map((x) => [x.narration, x.steps.map((y) => y.id)])).toEqual([
      ['', ['0']],
      ['A', ['1', '2']],
      ['B', ['3']],
    ])
  })

  it('status line prefers the running tool, then progress, then the last step', () => {
    const run = s('r', { status: 'running', name: 'file_read', argsText: '{"path":"/a/b/c.txt"}' })
    expect(liveStatusLine([s('1'), run])).toEqual({ tool: 'Read', text: '…/b/c.txt' })
    expect(liveStatusLine([s('1')], { progress: { label: 'Taking that in…', step: 3, total: 9 } })).toEqual({
      text: 'Taking that in… 3/9',
    })
    expect(liveStatusLine([], { thinking: true }).text).toBe('Thinking…')
    expect(liveStatusLine([]).text).toBe('Starting…')
  })

  it('formats elapsed time and progress', () => {
    expect(formatElapsed(6400)).toBe('0:06')
    expect(formatElapsed(125000)).toBe('2:05')
    expect(progressPercent({ step: 18, total: 36 })).toBe(50)
    expect(progressPercent({ percent: 140 })).toBe(100)
    expect(progressPercent(null)).toBeNull()
    expect(stepsElapsed([s('a', { startedAt: 1000, endedAt: 4000 }), s('b', { startedAt: 2000, endedAt: 7000 })], 0)).toBe(6000)
    expect(stepsElapsed([s('a', { startedAt: 1000, status: 'running' })], 5000)).toBe(4000)
  })
})
