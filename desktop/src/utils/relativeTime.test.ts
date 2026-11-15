import { describe, expect, it } from 'vitest'
import { dayLabel, relativeTime } from './relativeTime'

const now = Date.UTC(2026, 0, 15, 12, 0, 0)

describe('relativeTime', () => {
  it('keeps compact English when no locale is passed', () => {
    expect(relativeTime(new Date(now - 3_000).toISOString(), now)).toBe('just now')
    expect(relativeTime(new Date(now - 3 * 60_000).toISOString(), now)).toBe('3m ago')
    expect(dayLabel(new Date(now).toISOString(), now)).toBe('Today')
    expect(dayLabel(new Date(now - 86_400_000).toISOString(), now)).toBe('Yesterday')
  })

  it('keeps compact English for an English locale', () => {
    expect(relativeTime(new Date(now - 3_000).toISOString(), now, 'en')).toBe('just now')
    expect(dayLabel(new Date(now).toISOString(), now, 'en')).toBe('Today')
  })

  it('follows a non-English locale', () => {
    const just = relativeTime(new Date(now - 3_000).toISOString(), now, 'es')
    const mins = relativeTime(new Date(now - 3 * 60_000).toISOString(), now, 'de')
    const today = dayLabel(new Date(now).toISOString(), now, 'ja')
    expect(just).not.toBe('just now')
    expect(mins).not.toBe('3m ago')
    expect(today).not.toBe('Today')
    expect(just.length).toBeGreaterThan(0)
    expect(mins.length).toBeGreaterThan(0)
    expect(today.length).toBeGreaterThan(0)
  })
})
