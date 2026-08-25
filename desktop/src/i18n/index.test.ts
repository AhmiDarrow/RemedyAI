import { describe, expect, it } from 'vitest'
import { EN, formatMsg } from './en'

describe('chrome catalog', () => {
  it('covers the default-surface keys', () => {
    for (const key of [
      'bar.help',
      'menu.settings',
      'composer.placeholder',
      'settings.language',
      'settings.save',
      'sidebar.newSession',
      'approval.once',
      'userName.title',
      'grove.alongside',
      'settings.title',
      'setup.getStarted',
      'plan.approve',
      'quit.confirm',
      'empty.jump',
    ]) {
      expect(EN[key]).toBeTruthy()
    }
  })

  it('fills about-version templates', () => {
    expect(formatMsg(EN['menu.aboutVersion']!, { version: '0.38.1' })).toBe(
      'About Remedy (v0.38.1)',
    )
  })
})
