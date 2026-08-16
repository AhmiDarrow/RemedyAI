import { describe, expect, it } from 'vitest'
import { sanitizeAssistantText } from './sanitizeChat'

describe('sanitizeAssistantText', () => {
  it('strips empty or returns string', () => {
    expect(typeof sanitizeAssistantText('hello')).toBe('string')
    expect(sanitizeAssistantText('hello world')).toContain('hello')
  })

  it('handles empty', () => {
    expect(sanitizeAssistantText('')).toBe('')
  })
})
