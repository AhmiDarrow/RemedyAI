/** UI prefs for density + custom accent (local). */

export type Density = 'cozy' | 'compact'

const DENSITY_KEY = 'remedy-density'
const ACCENT_KEY = 'remedy-custom-accent'

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
