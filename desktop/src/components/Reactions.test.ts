import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ReactionPicker, ReactionPills } from './Reactions'

const noop = () => {}

describe('ReactionPills', () => {
  it('renders nothing without reactions', () => {
    expect(renderToStaticMarkup(createElement(ReactionPills, { messageId: 'a', partnerName: 'Remedy', align: 'start' }))).toBe('')
  })

  it("labels the owner's removable pill and Remedy's pill", () => {
    const html = renderToStaticMarkup(
      createElement(ReactionPills, {
        messageId: 'a',
        reactions: { user: '❤️', agent: '👍' },
        partnerName: 'Remedy',
        onReact: noop,
        align: 'start',
      }),
    )
    expect(html).toContain('<button')
    expect(html).toContain('aria-label="You reacted Heart. Press to remove."')
    expect(html).toContain('aria-label="Remedy reacted Thumbs up"')
  })

  it('ignores anything outside the four', () => {
    const html = renderToStaticMarkup(
      createElement(ReactionPills, { messageId: 'a', reactions: { agent: '🎉' }, partnerName: 'Remedy', align: 'end' }),
    )
    expect(html).toBe('')
  })
})

describe('ReactionPicker', () => {
  it('is a labelled toggle when closed', () => {
    const html = renderToStaticMarkup(
      createElement(ReactionPicker, { messageId: 'a', open: false, onOpenChange: noop, onReact: noop }),
    )
    expect(html).toContain('aria-label="Add reaction"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('role="toolbar"')
  })

  it('shows the four options with the current one pressed', () => {
    const html = renderToStaticMarkup(
      createElement(ReactionPicker, { messageId: 'a', current: '👎', open: true, onOpenChange: noop, onReact: noop }),
    )
    expect(html).toContain('role="toolbar"')
    expect(html.match(/class="reaction-option/g)?.length).toBe(4)
    expect(html).toContain('aria-label="Thumbs down (selected, press to remove)"')
    expect(html.match(/aria-pressed="true"/g)?.length).toBe(1)
  })
})
