import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { StatusBar } from './StatusBar'
import { getResolvedTheme } from '../themes'

const noop = () => {}
const props = {
  sessionId: null, streaming: false, model: 'Preview model', provider: 'demo',
  thinkingLevel: 'high' as const, approvalMode: 'ask' as const,
  themeId: 'forest' as const, theme: getResolvedTheme('forest'),
  onThemeChange: noop, planMode: false, onTogglePlanMode: noop,
  onTogglePanel: noop, updateAvailable: false, onCheckUpdates: noop,
  onProviderModelChange: noop,
}

describe('Simple control bar', () => {
  it('offers one named model dialog instead of showing the provider and model selectors', () => {
    const html = renderToStaticMarkup(createElement(StatusBar, { ...props, uiMode: 'simple' }))
    expect(html).toContain('Model options: Preview model')
    expect(html).toContain('aria-haspopup="dialog"')
    expect(html).not.toContain('settings-select')
  })

  it('keeps direct model controls in Advanced', () => {
    const html = renderToStaticMarkup(createElement(StatusBar, { ...props, uiMode: 'advanced' }))
    expect(html).not.toContain('Model options: Preview model')
    expect(html).toContain('aria-haspopup="listbox"')
    expect(html).toContain('Ask before risky actions')
  })
})
