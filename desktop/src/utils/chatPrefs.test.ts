import { describe, expect, it } from 'vitest'
import { isFontScale, stepFontScale } from './chatPrefs'

describe('font scale', () => {
  it('accepts known sizes', () => {
    expect(isFontScale('sm')).toBe(true)
    expect(isFontScale('md')).toBe(true)
    expect(isFontScale('xl')).toBe(true)
    expect(isFontScale('huge')).toBe(false)
  })

  it('steps without leaving the range', () => {
    expect(stepFontScale('md', 1)).toBe('lg')
    expect(stepFontScale('xl', 1)).toBe('xl')
    expect(stepFontScale('sm', -1)).toBe('sm')
    expect(stepFontScale('lg', -1)).toBe('md')
  })
})
