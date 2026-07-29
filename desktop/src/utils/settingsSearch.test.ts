import { describe, expect, it } from 'vitest'
import { sectionMatchesSearch } from '../components/SettingsSection'
import { SETTINGS_SECTION_META } from './settingsSearch'

describe('settings search', () => {
  it('matches security section by keyword', () => {
    const m = SETTINGS_SECTION_META['security-power']
    expect(sectionMatchesSearch('approval', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('xyzzy-nope', m.title, m.summary, m.keywords)).toBe(false)
  })

  it('empty query matches all', () => {
    const m = SETTINGS_SECTION_META.theme
    expect(sectionMatchesSearch('', m.title, m.summary, m.keywords)).toBe(true)
  })

  it('matches personal assistant section by keyword', () => {
    const m = SETTINGS_SECTION_META.assistant
    expect(sectionMatchesSearch('budget', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('gmail', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('xyzzy-nope', m.title, m.summary, m.keywords)).toBe(false)
  })
})
