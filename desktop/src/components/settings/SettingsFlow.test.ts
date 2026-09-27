import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SettingsFlow, SettingsDialog } from './SettingsFlow'

describe('focused settings flows', () => {
  it('offers a named dialog without mounting setup fields before the owner opens it', () => {
    const html = renderToStaticMarkup(createElement(SettingsFlow, {
      title: 'Connection setup', summary: 'Network address and access away from home',
      children: createElement('input', { 'aria-label': 'Private setup field' }),
    }))
    expect(html).toContain('aria-haspopup="dialog"')
    expect(html).toContain('Connection setup')
    expect(html).not.toContain('Private setup field')
    expect(html).not.toContain('role="dialog"')
  })

  it('keeps an unopened dialog out of the document and accessibility tree', () => {
    expect(renderToStaticMarkup(createElement(SettingsDialog, {
      open: false, title: 'Advanced permissions', onClose: () => {}, children: 'Setup content',
    }))).toBe('')
  })
})
