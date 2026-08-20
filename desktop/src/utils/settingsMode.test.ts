import { describe, expect, it } from 'vitest'
import { ADVANCED_ONLY_SECTIONS, isSectionVisibleInMode } from './settingsMode'

describe('settingsMode', () => {
  it('simple hides advanced-only sections', () => {
    expect(isSectionVisibleInMode('provider', 'simple')).toBe(true)
    expect(isSectionVisibleInMode('assistant', 'simple')).toBe(true)
    expect(isSectionVisibleInMode('voice', 'simple')).toBe(true)
    expect(isSectionVisibleInMode('privacy', 'simple')).toBe(true)
    expect(isSectionVisibleInMode('vision', 'simple')).toBe(false)
    expect(isSectionVisibleInMode('security-power', 'simple')).toBe(false)
  })

  it('advanced shows all', () => {
    for (const id of ADVANCED_ONLY_SECTIONS) {
      expect(isSectionVisibleInMode(id, 'advanced')).toBe(true)
    }
  })
})
