/** Fallback OS-voice picker matches the assigned gender role. */

import { describe, expect, it } from 'vitest'
import { pickFallbackVoice, type NamedVoice } from './pickVoice'

const WINDOWS_VOICES: NamedVoice[] = [
  { name: 'Microsoft David - English (United States)', lang: 'en-US', default: true },
  { name: 'Microsoft Zira - English (United States)', lang: 'en-US' },
  { name: 'Microsoft Mark - English (United States)', lang: 'en-US' },
  { name: 'Microsoft Hazel - English (Great Britain)', lang: 'en-GB' },
  { name: 'Microsoft Hortense - French (France)', lang: 'fr-FR' },
]

describe('pickFallbackVoice', () => {
  it('picks a female-named voice for the female role (despite male default)', () => {
    const v = pickFallbackVoice(WINDOWS_VOICES, 'female')
    expect(v?.name).toContain('Zira')
  })

  it('picks a male-named voice for the male role', () => {
    const v = pickFallbackVoice(WINDOWS_VOICES, 'male')
    expect(v?.name).toMatch(/David|Mark/)
  })

  it('prefers Natural/Neural voices when present', () => {
    const v = pickFallbackVoice(
      [
        ...WINDOWS_VOICES,
        { name: 'Microsoft Aria Online (Natural) - English (United States)', lang: 'en-US' },
      ],
      'female',
    )
    expect(v?.name).toContain('Aria')
  })

  it('neutral avoids strongly gender-named voices when possible', () => {
    const v = pickFallbackVoice(
      [
        { name: 'Microsoft Zira - English (United States)', lang: 'en-US' },
        { name: 'English (America) espeak', lang: 'en-US' },
      ],
      'neutral',
    )
    expect(v?.name).not.toContain('Zira')
  })

  it('handles empty list', () => {
    expect(pickFallbackVoice([], 'female')).toBeNull()
  })

  it('prefers English voices over the system default language', () => {
    const v = pickFallbackVoice(
      [
        { name: 'Microsoft Hortense - French (France)', lang: 'fr-FR', default: true },
        { name: 'Microsoft Zira - English (United States)', lang: 'en-US' },
      ],
      'female',
    )
    expect(v?.lang).toBe('en-US')
  })
})
