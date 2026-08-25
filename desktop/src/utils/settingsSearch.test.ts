import { describe, expect, it } from 'vitest'
import { sectionMatchesSearch } from '../components/SettingsSection'
import { SETTINGS_SECTION_META } from './settingsSearch'

describe('settings search', () => {
  it('matches security section by keyword', () => {
    const m = SETTINGS_SECTION_META['security-power']
    expect(sectionMatchesSearch('approval', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('trust', m.title, m.summary, m.keywords)).toBe(true)
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

  it('matches local models section by huggingface keyword', () => {
    const m = SETTINGS_SECTION_META.rmb
    expect(sectionMatchesSearch('hugging face', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('gguf', m.title, m.summary, m.keywords)).toBe(true)
  })

  it('matches appearance section by accessibility keywords', () => {
    const m = SETTINGS_SECTION_META.theme
    expect(sectionMatchesSearch('font size', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('contrast', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('appearance', m.title, m.summary, m.keywords)).toBe(true)
  })

  it('matches voice section by keyword', () => {
    const m = SETTINGS_SECTION_META.voice
    expect(sectionMatchesSearch('kokoro', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('aloud', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('chatterbox', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('xyzzy-nope', m.title, m.summary, m.keywords)).toBe(false)
  })

  it('matches privacy section by keyword', () => {
    const m = SETTINGS_SECTION_META.privacy
    expect(sectionMatchesSearch('email', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('pii', m.title, m.summary, m.keywords)).toBe(true)
    expect(sectionMatchesSearch('xyzzy-nope', m.title, m.summary, m.keywords)).toBe(false)
  })
})
