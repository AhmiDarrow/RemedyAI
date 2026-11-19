import { describe, expect, it } from 'vitest'
import { needsOwner, relativeWhen, scheduleFromChoice, stateWords } from '../api/head'
import {
  aboutChild,
  bubbleSlice,
  childPollMs,
  clampBubble,
  explainCommand,
  isGenericReason,
  orderDecisions,
} from './headHelpers'
import type { ChatMessage } from '../types'

describe('head state words', () => {
  it('speaks every state in plain words, never the raw token', () => {
    const states = ['idle', 'queued', 'working', 'waiting', 'blocked', 'done', 'stopped', 'error', '', undefined] as const
    for (const s of states) {
      const w = stateWords(s)
      expect(w).toBeTruthy()
      expect(w).not.toMatch(/^(queued|blocked|error|idle)$/)
    }
    expect(stateWords('waiting')).toBe(stateWords('blocked'))
  })

  it('flags only the states that need the owner', () => {
    expect(needsOwner('waiting')).toBe(true)
    expect(needsOwner('blocked')).toBe(true)
    expect(needsOwner('error')).toBe(true)
    for (const s of ['working', 'queued', 'done', 'stopped', 'idle', '', undefined] as const) {
      expect(needsOwner(s)).toBe(false)
    }
  })
})

describe('relativeWhen', () => {
  const now = Date.parse('2026-11-19T12:00:00Z')
  it('covers future, past, and junk', () => {
    expect(relativeWhen('2026-11-19T12:00:20Z', now)).toBe('now')
    expect(relativeWhen('2026-11-19T11:59:40Z', now)).toBe('just now')
    expect(relativeWhen('2026-11-19T12:12:00Z', now)).toBe('in 12m')
    expect(relativeWhen('2026-11-19T15:00:00Z', now)).toBe('in 3h')
    expect(relativeWhen('2026-11-17T12:00:00Z', now)).toBe('2d ago')
    expect(relativeWhen('not a date', now)).toBe('')
    expect(relativeWhen(undefined, now)).toBe('')
  })
})

describe('scheduleFromChoice', () => {
  it('maps the dialog choices onto runtime schedules', () => {
    expect(scheduleFromChoice('hourly', {})).toEqual({ kind: 'every', every_minutes: 60 })
    expect(scheduleFromChoice('every', { everyHours: 3 })).toEqual({ kind: 'every', every_minutes: 180 })
    expect(scheduleFromChoice('every', { everyHours: 0 })).toEqual({ kind: 'every', every_minutes: 60 })
    expect(scheduleFromChoice('weekdays', { at: '07:15' })).toEqual({ kind: 'weekdays', at: '07:15' })
    expect(scheduleFromChoice('daily', {})).toEqual({ kind: 'daily', at: '08:00' })
    expect(scheduleFromChoice('weekly', { at: '09:00', days: [] })).toEqual({ kind: 'weekly', at: '09:00', days: ['mon'] })
    const once = scheduleFromChoice('once', { onceLocal: '2026-12-01T10:30' })
    expect(once.kind).toBe('once')
    expect(once.once_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
  })
})

describe('child room', () => {
  it('names the job when the owner speaks to Remedy from a child room', () => {
    expect(aboutChild('Flights', '  cheaper please ')).toBe('About the side job “Flights”: cheaper please')
  })

  it('polls fast only while the child is working', () => {
    expect(childPollMs('working')).toBeLessThan(childPollMs('done'))
    expect(childPollMs('queued')).toBe(childPollMs('working'))
    expect(childPollMs('waiting')).toBe(childPollMs('done'))
  })

  const msg = (id: string, role: string, content: unknown): ChatMessage =>
    ({ id, role, content }) as unknown as ChatMessage

  it('bubble shows only what follows the anchor, text only', () => {
    const thread = [
      msg('a', 'user', 'old'),
      msg('b', 'assistant', 'old reply'),
      msg('c', 'user', 'About the side job “F”: hi'),
      msg('d', 'tool', 'ignored'),
      msg('e', 'assistant', '   '),
      msg('f', 'assistant', 'Sure — on it'),
    ]
    expect(bubbleSlice(thread, 'b').map((m) => m.id)).toEqual(['c', 'f'])
    expect(bubbleSlice(thread, 'b')[0].text).toBe('hi')
    expect(bubbleSlice(thread, '').map((m) => m.id)).toEqual(['a', 'b', 'c', 'f'])
    // An anchor that scrolled out of the window shows the whole window.
    expect(bubbleSlice(thread, 'gone').map((m) => m.id)).toEqual(['a', 'b', 'c', 'f'])
  })
})

describe('bubble position', () => {
  it('stays on screen', () => {
    expect(clampBubble({ x: -50, y: -50 }, 1200, 800)).toEqual({ x: 8, y: 8 })
    const far = clampBubble({ x: 5000, y: 5000 }, 1200, 800)
    expect(far.x).toBeLessThanOrEqual(1200 - 340 - 8)
    expect(far.y).toBeLessThanOrEqual(800 - 60)
    // A window narrower than the bubble pins it to the padding, never negative.
    expect(clampBubble({ x: 100, y: 100 }, 200, 800).x).toBe(8)
  })
})

describe('decision order', () => {
  it('oldest first, pending only, no internal drive rows', () => {
    const got = orderDecisions([
      { id: 'b', created_at: 20, status: 'pending' },
      { id: 'drive', tool_name: 'life_drive', created_at: 1, status: 'pending' },
      { id: 'a', created_at: 10, status: 'pending' },
      { id: 'old', created_at: 5, status: 'approved' },
    ])
    expect(got.map((i) => i.id)).toEqual(['a', 'b'])
  })
})

describe('decision card explain', () => {
  it('drops runtime-bound fields and labels the rest', () => {
    const rows = explainCommand(
      '{"caller_origin":"","session_id":"s1","label":"Phone plans","task":"Compare plans","schedule":{"kind":"daily"}}',
    )
    expect(rows).toEqual([
      { label: 'Label', value: 'Phone plans' },
      { label: 'Task', value: 'Compare plans' },
      { label: 'Schedule', value: '{"kind":"daily"}' },
    ])
  })
  it('falls back to the raw text for non-JSON or empty objects', () => {
    expect(explainCommand('git push origin main')).toBe('git push origin main')
    expect(explainCommand('{not json')).toBe('{not json')
    expect(explainCommand('{"session_id":"x"}')).toBe('{"session_id":"x"}')
  })
  it('treats boilerplate reasons as nothing to say', () => {
    expect(isGenericReason('Tool requires your approval')).toBe(true)
    expect(isGenericReason('')).toBe(true)
    expect(isGenericReason('Payment step — card ending 4242')).toBe(false)
  })
})
