import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_FONTS,
  FONT_ROLES,
  applyFonts,
  facesForRole,
  fontStack,
  loadFonts,
  resetFonts,
  saveFont,
} from './fontPrefs'

// Node test env: minimal stand-ins for localStorage and the root style.
const store = new Map<string, string>()
const props = new Map<string, string>()
Object.assign(globalThis, {
  localStorage: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  },
  document: {
    documentElement: {
      style: {
        setProperty: (k: string, v: string) => void props.set(k, v),
        getPropertyValue: (k: string) => props.get(k) ?? '',
      },
    },
  },
})

describe('fontPrefs', () => {
  beforeEach(() => {
    store.clear()
    props.clear()
  })

  it('defaults use Remedy Sans UI for text and AhmiPrint for code', () => {
    expect(loadFonts()).toEqual(DEFAULT_FONTS)
    expect(DEFAULT_FONTS.ui).toBe('remedy-sans-ui')
    expect(DEFAULT_FONTS.code).toBe('ahmi-print')
    expect(DEFAULT_FONTS.reading).toBe('ahmi-print')
  })

  it('applies every role as a CSS variable', () => {
    applyFonts()
    const st = document.documentElement.style
    expect(st.getPropertyValue('--font-ui')).toContain('Remedy Sans UI')
    expect(st.getPropertyValue('--font-heading')).toContain('Remedy Sans')
    expect(st.getPropertyValue('--font-reading')).toContain('AhmiPrint')
    expect(st.getPropertyValue('--font-accent')).toContain('Ahmi Pixel')
    expect(st.getPropertyValue('--font-code')).toContain('AhmiPrint')
  })

  it('saves one role without touching the others, and resets', () => {
    const next = saveFont('code', 'jetbrains-mono')
    expect(next.code).toBe('jetbrains-mono')
    expect(loadFonts().ui).toBe('remedy-sans-ui')
    expect(document.documentElement.style.getPropertyValue('--font-code')).toContain('JetBrains Mono')
    expect(resetFonts()).toEqual(DEFAULT_FONTS)
    expect(loadFonts()).toEqual(DEFAULT_FONTS)
  })

  it('ignores unknown stored faces', () => {
    localStorage.setItem('remedy-fonts', JSON.stringify({ ui: 'comic-sans', code: 'fira-code' }))
    const f = loadFonts()
    expect(f.ui).toBe('remedy-sans-ui')
    expect(f.code).toBe('fira-code')
  })

  it('offers AhmiPrint first for code, then monospace faces, and keeps fallbacks', () => {
    expect(facesForRole('code')[0].id).toBe('ahmi-print')
    expect(facesForRole('code').some((f) => f.mono)).toBe(true)
    expect(facesForRole('ui')[0].id).toBe('remedy-sans-ui')
    expect(fontStack('ahmi-print')).toContain('serif')
  })
})
