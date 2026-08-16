import { describe, expect, it } from 'vitest'
import { stampMarkupFilename, MARKUP_COLORS, MARKUP_WIDTHS } from './imageMarkup'

describe('imageMarkup helpers', () => {
  it('stamps a safe png filename', () => {
    const name = stampMarkupFilename('My Screenshot!.png')
    expect(name.endsWith('.png')).toBe(true)
    expect(name).toMatch(/^My_Screenshot[_-]*markup-/)
    expect(name).not.toMatch(/[<>:"/\\|?*]/)
  })

  it('falls back when alt empty', () => {
    expect(stampMarkupFilename('')).toMatch(/^image-markup-.*\.png$/)
  })

  it('exports palette and widths', () => {
    expect(MARKUP_COLORS.length).toBeGreaterThanOrEqual(5)
    expect(MARKUP_WIDTHS).toContain(4)
  })
})
