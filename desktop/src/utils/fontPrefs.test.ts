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

  it('defaults give each role its own face, Remedy Sans UI for text', () => {
    expect(loadFonts()).toEqual(DEFAULT_FONTS)
    expect(DEFAULT_FONTS.ui).toBe('remedy-sans-ui')
    expect(new Set(Object.values(DEFAULT_FONTS)).size).toBe(FONT_ROLES.length)
  })

  it('applies every role as a CSS variable', () => {
    applyFonts()
    const st = document.documentElement.style
    expect(st.getPropertyValue('--font-ui')).toContain('Remedy Sans UI')
    expect(st.getPropertyValue('--font-heading')).toContain('Remedy Sans')
    expect(st.getPropertyValue('--font-reading')).toContain('AhmiPrint')
    expect(st.getPropertyValue('--font-accent')).toContain('Ahmi Pixel')
    expect(st.getPropertyValue('--font-code')).toContain('Cascadia Code')
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

  it('offers monospace faces first for code and keeps fallbacks', () => {
    expect(facesForRole('code')[0].mono).toBe(true)
    expect(facesForRole('ui')[0].id).toBe('remedy-sans-ui')
    expect(fontStack('ahmi-print')).toContain('serif')
  })
})
