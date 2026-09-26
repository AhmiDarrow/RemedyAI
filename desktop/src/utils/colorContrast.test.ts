import { describe, expect, it } from 'vitest'
import { THEMES } from '../themes'
import { contrastingText, contrastRatio, luminance } from './colorContrast'

describe('readable foregrounds', () => {
  it('handles short hex and channel extremes', () => {
    expect(luminance('#fff')).toBe(1)
    expect(luminance('#000')).toBe(0)
    expect(contrastRatio('#fff', '#000')).toBe(21)
  })
  it('keeps user messages readable in every palette', () => {
    for (const theme of Object.values(THEMES)) {
      expect(contrastRatio(theme.colors['--chat-user-bg'], theme.colors['--chat-user-fg']), theme.id).toBeGreaterThanOrEqual(4.5)
    }
  })
  it('supports custom accents across the RGB cube', () => {
    for (let r = 0; r <= 255; r += 17) for (let g = 0; g <= 255; g += 17) for (let b = 0; b <= 255; b += 17) {
      const color = '#' + [r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')
      expect(contrastRatio(color, contrastingText(color))).toBeGreaterThanOrEqual(4.5)
    }
  })
})
