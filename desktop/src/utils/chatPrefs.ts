/** UI prefs for density, accent, and accessibility (local). */

export type Density = 'cozy' | 'compact'
export type FontScale = 'sm' | 'md' | 'lg' | 'xl'

export const FONT_SCALE_OPTIONS: { id: FontScale; label: string; factor: number }[] = [
  { id: 'sm', label: 'S', factor: 0.9 },
  { id: 'md', label: 'M', factor: 1 },
  { id: 'lg', label: 'L', factor: 1.15 },
  { id: 'xl', label: 'XL', factor: 1.3 },
]

const FONT_ORDER: FontScale[] = ['sm', 'md', 'lg', 'xl']

const DENSITY_KEY = 'remedy-density'
const ACCENT_KEY = 'remedy-custom-accent'
const FONT_KEY = 'remedy-font-scale'
const MOTION_KEY = 'remedy-reduce-motion'
const CONTRAST_KEY = 'remedy-high-contrast'

export function loadDensity(): Density {
  try {
    const v = localStorage.getItem(DENSITY_KEY)
    if (v === 'compact' || v === 'cozy') return v
  } catch {
    /* */
  }
  return 'cozy'
}

export function saveDensity(d: Density) {
  try {
    localStorage.setItem(DENSITY_KEY, d)
  } catch {
    /* */
  }
}

export function applyDensity(d: Density) {
  document.documentElement.setAttribute('data-density', d)
}

/** Empty string = use theme default accent. */
export function loadCustomAccent(): string {
  try {
    const v = localStorage.getItem(ACCENT_KEY)
    if (v && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) return v
  } catch {
    /* */
  }
  return ''
}

export function saveCustomAccent(hex: string) {
  try {
    if (!hex) localStorage.removeItem(ACCENT_KEY)
    else localStorage.setItem(ACCENT_KEY, hex)
  } catch {
    /* */
  }
}

function hoverFrom(hex: string): string {
  // Slightly darken for hover — simple RGB mix toward black.
  const h = hex.replace('#', '')
  const full =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h
  const n = parseInt(full, 16)
  if (Number.isNaN(n)) return hex
  const r = Math.max(0, ((n >> 16) & 255) - 18)
  const g = Math.max(0, ((n >> 8) & 255) - 18)
  const b = Math.max(0, (n & 255) - 18)
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, '0')).join('')}`
}

/** Apply or clear custom accent override on CSS variables. */
export function applyCustomAccent(hex: string) {
  const root = document.documentElement
  if (!hex) {
    root.style.removeProperty('--custom-accent')
    // Re-apply from theme is handled by applyTheme; just clear overrides
    // by re-setting from computed data if present.
    return
  }
  root.style.setProperty('--accent', hex)
  root.style.setProperty('--accent-hover', hoverFrom(hex))
  root.style.setProperty('--chat-user-bg', hex)
  root.style.setProperty('--chat-user-border', hex)
  root.style.setProperty('--custom-accent', hex)
}

export function isFontScale(v: string | null | undefined): v is FontScale {
  return v === 'sm' || v === 'md' || v === 'lg' || v === 'xl'
}

export function loadFontScale(): FontScale {
  try {
    const v = localStorage.getItem(FONT_KEY)
    if (isFontScale(v)) return v
  } catch {
    /* */
  }
  return 'md'
}

export function saveFontScale(s: FontScale) {
  try {
    localStorage.setItem(FONT_KEY, s)
  } catch {
    /* */
  }
}

export function applyFontScale(s: FontScale) {
  const root = document.documentElement
  root.setAttribute('data-font-scale', s)
  const factor = FONT_SCALE_OPTIONS.find((o) => o.id === s)?.factor ?? 1
  root.style.setProperty('--ui-font-scale', String(factor))
}

export function stepFontScale(current: FontScale, dir: 1 | -1): FontScale {
  const i = FONT_ORDER.indexOf(current)
  const next = Math.max(0, Math.min(FONT_ORDER.length - 1, i + dir))
  return FONT_ORDER[next]
}

export function loadReduceMotion(): boolean {
  try {
    const v = localStorage.getItem(MOTION_KEY)
    if (v === '1') return true
    if (v === '0') return false
  } catch {
    /* */
  }
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function saveReduceMotion(on: boolean) {
  try {
    localStorage.setItem(MOTION_KEY, on ? '1' : '0')
  } catch {
    /* */
  }
}

export function applyReduceMotion(on: boolean) {
  document.documentElement.setAttribute('data-reduce-motion', on ? '1' : '0')
}

export function loadHighContrast(): boolean {
  try {
    return localStorage.getItem(CONTRAST_KEY) === '1'
  } catch {
    return false
  }
}

export function saveHighContrast(on: boolean) {
  try {
    if (on) localStorage.setItem(CONTRAST_KEY, '1')
    else localStorage.removeItem(CONTRAST_KEY)
  } catch {
    /* */
  }
}

export function applyHighContrast(on: boolean) {
  document.documentElement.setAttribute('data-contrast', on ? 'high' : 'normal')
}

/** Apply stored chrome prefs before first paint (and after loads). */
export function applyStoredUiPrefs() {
  applyDensity(loadDensity())
  applyFontScale(loadFontScale())
  applyReduceMotion(loadReduceMotion())
  applyHighContrast(loadHighContrast())
  const accent = loadCustomAccent()
  if (accent) applyCustomAccent(accent)
}
