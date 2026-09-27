import { describe, expect, it } from 'vitest'
import { SETTINGS_SECTION_META, type SettingsSectionId } from './settingsSearch'
import { SETTINGS_CATEGORIES, SECTION_CATEGORY, isSettingsSection, settingsSectionVisible } from './settingsNavigation'

const sections = Object.keys(SETTINGS_SECTION_META) as SettingsSectionId[]

describe('settings navigation', () => {
  it('keeps every section reachable in exactly one advanced category', () => {
    for (const id of sections) {
      expect(SETTINGS_CATEGORIES.filter(category =>
        settingsSectionVisible(id, category, 'advanced', false, false),
      )).toEqual([SECTION_CATEGORY[id]])
    }
  })

  it('starts with a small set of everyday preferences', () => {
    expect(sections.filter(id => settingsSectionVisible(id, 'general', 'simple', false, true)))
      .toEqual(['you-agent', 'voice', 'workspace', 'theme'])
  })

  it('keeps privacy and approval controls accessible in simple mode', () => {
    expect(settingsSectionVisible('privacy', 'privacy', 'simple', false, true)).toBe(true)
    expect(settingsSectionVisible('security-power', 'privacy', 'simple', false, true)).toBe(true)
    expect(settingsSectionVisible('access', 'privacy', 'simple', false, true)).toBe(false)
  })

  it('searches all categories and advanced sections without changing the selected mode', () => {
    for (const category of SETTINGS_CATEGORIES) {
      for (const id of sections) {
        expect(settingsSectionVisible(id, category, 'simple', true, true)).toBe(true)
        expect(settingsSectionVisible(id, category, 'advanced', true, false)).toBe(false)
      }
    }
  })

  it('rejects stale or malformed section links', () => {
    for (const id of [null, '', 'unknown', 'constructor', '__proto__', 'toString']) {
      expect(isSettingsSection(id)).toBe(false)
    }
    for (const id of sections) expect(isSettingsSection(id)).toBe(true)
  })
})
