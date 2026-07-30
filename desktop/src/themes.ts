export type ThemeId =
  | 'system'
  | 'dark'
  | 'neutral'
  | 'light'
  | 'forest'
  | 'green'
  | 'purple'
  | 'alien'
  | 'orange'
  | 'cyan'

/**
 * Default theme for first install / first run (no localStorage preference).
 * Always Dark Forest — not System, not classic purple Dark.
 */
export const DEFAULT_THEME_ID: ThemeId = 'forest'

const THEME_ID_SET = new Set<string>([
  'system',
  'dark',
  'neutral',
  'light',
  'forest',
  'green',
  'purple',
  'alien',
  'orange',
  'cyan',
])

/** True if *id* is a known theme preference (including System). */
export function isThemeId(id: string | null | undefined): id is ThemeId {
  return typeof id === 'string' && THEME_ID_SET.has(id)
}

export interface ThemeColors {
  '--bg-primary': string
  '--bg-secondary': string
  '--bg-tertiary': string
  '--border': string
  '--accent': string
  '--accent-hover': string
  '--text-primary': string
  '--text-secondary': string
  '--text-muted': string
  '--success': string
  '--error': string
  '--warning': string
  /** Chat bubbles — user right / assistant left */
  '--chat-user-bg': string
  '--chat-user-fg': string
  '--chat-user-border': string
  '--chat-assistant-bg': string
  '--chat-assistant-fg': string
  '--chat-assistant-border': string
  '--chat-system-bg': string
  '--chat-system-fg': string
  '--chat-system-border': string
  '--chat-bubble-radius': string
  '--chat-max-width': string
}

/** Shared chat geometry (colors differ per theme). */
const CHAT_GEOMETRY = {
  '--chat-bubble-radius': '0.65rem',
  '--chat-max-width': '72%',
} as const

function chatFromPalette(
  kind: 'dark' | 'light',
  accent: string,
  secondary: string,
  tertiary: string,
  border: string,
  textPrimary: string,
  error: string,
): Pick<
  ThemeColors,
  | '--chat-user-bg'
  | '--chat-user-fg'
  | '--chat-user-border'
  | '--chat-assistant-bg'
  | '--chat-assistant-fg'
  | '--chat-assistant-border'
  | '--chat-system-bg'
  | '--chat-system-fg'
  | '--chat-system-border'
  | '--chat-bubble-radius'
  | '--chat-max-width'
> {
  return {
    // Keep chat user bubble = theme accent so swatches match what you see in chat.
    '--chat-user-bg': accent,
    '--chat-user-fg': kind === 'light' ? '#ffffff' : '#ffffff',
    '--chat-user-border': accent,
    '--chat-assistant-bg': secondary,
    '--chat-assistant-fg': textPrimary,
    '--chat-assistant-border': border,
    '--chat-system-bg': tertiary,
    '--chat-system-fg': error,
    '--chat-system-border': border,
    ...CHAT_GEOMETRY,
  }
}

/** Colors for theme picker dots — always taken from the real palette. */
export type ThemeSwatch = {
  /** Main surface (dark/light character of the theme) */
  bg: string
  /** Signature accent */
  accent: string
  /** Secondary surface for multi-tone rings */
  surface: string
  /** Border tone for the swatch outline */
  border: string
}

export function themeSwatch(id: ThemeId): ThemeSwatch {
  if (id === 'system') {
    // Split preview: left follows OS dark, right follows OS light accent.
    const dark = THEMES.dark.colors
    const light = THEMES.light.colors
    const osLight = systemPrefersLight()
    const active = osLight ? light : dark
    return {
      bg: active['--bg-primary'],
      accent: active['--accent'],
      surface: active['--bg-secondary'],
      border: active['--border'],
    }
  }
  const c = THEMES[id].colors
  return {
    bg: c['--bg-primary'],
    accent: c['--accent'],
    surface: c['--bg-secondary'],
    border: c['--border'],
  }
}

/** System option shows dual dark/light halves so it isn't confused with Dark. */
export function systemThemeSwatch(): { dark: ThemeSwatch; light: ThemeSwatch } {
  const d = THEMES.dark.colors
  const l = THEMES.light.colors
  return {
    dark: {
      bg: d['--bg-primary'],
      accent: d['--accent'],
      surface: d['--bg-secondary'],
      border: d['--border'],
    },
    light: {
      bg: l['--bg-primary'],
      accent: l['--accent'],
      surface: l['--bg-secondary'],
      border: l['--border'],
    },
  }
}

export interface Theme {
  id: ThemeId
  name: string
  kind: 'dark' | 'light'
  /** Resolved palette themes only — system uses resolved dark/light */
  colors: ThemeColors
}

/** Concrete palettes tuned for long reading (strong body contrast, softer muted). */
export const THEMES: Record<Exclude<ThemeId, 'system'>, Theme> = {
  dark: {
    id: 'dark',
    name: 'Dark',
    kind: 'dark',
    colors: {
      // Classic violet dark (original Remedy look)
      '--bg-primary': '#0c0c14',
      '--bg-secondary': '#14141f',
      '--bg-tertiary': '#1c1c2a',
      '--border': '#2a2a3d',
      '--accent': '#8b5cf6',
      '--accent-hover': '#7c3aed',
      '--text-primary': '#f0f0f5',
      '--text-secondary': '#a8a8c0',
      '--text-muted': '#7a7a96',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#fbbf24',
      ...chatFromPalette('dark', '#8b5cf6', '#14141f', '#1c1c2a', '#2a2a3d', '#f0f0f5', '#f87171'),
    },
  },

  /** True slate dark — no purple so it is distinct from Amethyst / Dark violet. */
  neutral: {
    id: 'neutral',
    name: 'Neutral Dark',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0f1115',
      '--bg-secondary': '#161a20',
      '--bg-tertiary': '#1e2430',
      '--border': '#2e3644',
      '--accent': '#94a3b8',
      '--accent-hover': '#cbd5e1',
      '--text-primary': '#e2e8f0',
      '--text-secondary': '#94a3b8',
      '--text-muted': '#64748b',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#fbbf24',
      ...chatFromPalette('dark', '#94a3b8', '#161a20', '#1e2430', '#2e3644', '#e2e8f0', '#f87171'),
    },
  },

  light: {
    id: 'light',
    name: 'Light',
    kind: 'light',
    colors: {
      '--bg-primary': '#f4f4f8',
      '--bg-secondary': '#ffffff',
      '--bg-tertiary': '#e8e8ef',
      // Stronger borders for light mode contrast
      '--border': '#a8a8b8',
      '--accent': '#7c3aed',
      '--accent-hover': '#6d28d9',
      '--text-primary': '#14141f',
      '--text-secondary': '#3d3d52',
      '--text-muted': '#5c5c72',
      '--success': '#15803d',
      '--error': '#b91c1c',
      '--warning': '#b45309',
      ...chatFromPalette('light', '#7c3aed', '#ffffff', '#e8e8ef', '#a8a8b8', '#14141f', '#b91c1c'),
    },
  },

  /**
   * Dark structure (like classic Dark) with muted forest green — no neon mint.
   * Distinct from Emerald (brighter accent / more saturated greens).
   */
  forest: {
    id: 'forest',
    name: 'Dark Forest',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a0e0b',
      '--bg-secondary': '#121812',
      '--bg-tertiary': '#1a221c',
      '--border': '#2a352c',
      // Moss / canopy — readable, not electric
      '--accent': '#4d7a5a',
      '--accent-hover': '#3d6349',
      '--text-primary': '#e6ebe7',
      '--text-secondary': '#9aa89e',
      '--text-muted': '#6b7870',
      '--success': '#5a8f6a',
      '--error': '#b87a7a',
      '--warning': '#a89058',
      ...chatFromPalette('dark', '#4d7a5a', '#121812', '#1a221c', '#2a352c', '#e6ebe7', '#b87a7a'),
    },
  },

  green: {
    id: 'green',
    name: 'Emerald',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a1410',
      '--bg-secondary': '#0f1f18',
      '--bg-tertiary': '#163026',
      '--border': '#1f4a38',
      '--accent': '#34d399',
      '--accent-hover': '#10b981',
      // Calmer body text (less pure pastel-on-dark glow)
      '--text-primary': '#d8ebe2',
      '--text-secondary': '#8fb8a4',
      '--text-muted': '#5f8574',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fcd34d',
      ...chatFromPalette('dark', '#34d399', '#0f1f18', '#163026', '#1f4a38', '#d8ebe2', '#fca5a5'),
    },
  },

  purple: {
    id: 'purple',
    name: 'Amethyst',
    kind: 'dark',
    colors: {
      '--bg-primary': '#100a16',
      '--bg-secondary': '#1a1224',
      '--bg-tertiary': '#261a36',
      '--border': '#3d2a55',
      '--accent': '#c084fc',
      '--accent-hover': '#a855f7',
      '--text-primary': '#e4dff0',
      '--text-secondary': '#b0a0c8',
      '--text-muted': '#7d7096',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#c084fc', '#1a1224', '#261a36', '#3d2a55', '#e4dff0', '#fca5a5'),
    },
  },

  /**
   * Dark Purple — deep void base + electric “alien purple” accent.
   * Distinct from Amethyst (softer lilac) and classic Dark (milder violet).
   */
  alien: {
    id: 'alien',
    name: 'Dark Purple',
    kind: 'dark',
    colors: {
      '--bg-primary': '#07040f',
      '--bg-secondary': '#0f081a',
      '--bg-tertiary': '#1a0f2e',
      '--border': '#3b1d66',
      // Alien purple — saturated electric violet
      '--accent': '#b026ff',
      '--accent-hover': '#9d00ff',
      '--text-primary': '#f2e8ff',
      '--text-secondary': '#c4a0e8',
      '--text-muted': '#8a6aad',
      '--success': '#5eead4',
      '--error': '#ff6b9d',
      '--warning': '#f0abfc',
      ...chatFromPalette('dark', '#b026ff', '#0f081a', '#1a0f2e', '#3b1d66', '#f2e8ff', '#ff6b9d'),
    },
  },

  orange: {
    id: 'orange',
    name: 'Amber',
    kind: 'dark',
    colors: {
      '--bg-primary': '#14100a',
      '--bg-secondary': '#1f1810',
      '--bg-tertiary': '#2e2418',
      '--border': '#4a3a24',
      '--accent': '#fb923c',
      '--accent-hover': '#f97316',
      '--text-primary': '#efe6da',
      '--text-secondary': '#c4a88a',
      '--text-muted': '#8f7a60',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#fb923c', '#1f1810', '#2e2418', '#4a3a24', '#efe6da', '#fca5a5'),
    },
  },

  cyan: {
    id: 'cyan',
    name: 'Ocean',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a1216',
      '--bg-secondary': '#0f1c22',
      '--bg-tertiary': '#163038',
      '--border': '#1e4a55',
      '--accent': '#22d3ee',
      '--accent-hover': '#06b6d4',
      '--text-primary': '#d8eef2',
      '--text-secondary': '#8fb8c2',
      '--text-muted': '#5f8790',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#22d3ee', '#0f1c22', '#163038', '#1e4a55', '#d8eef2', '#fca5a5'),
    },
  },
} as const

export type ThemeListEntry = {
  id: ThemeId
  name: string
  kind: 'dark' | 'light' | 'system'
  colors: ThemeColors
}

/**
 * Flat menu order (no section headers):
 *  1. System
 *  2. Light
 *  3. Themes whose name does **not** start with "Dark" (except Neutral Dark)
 *  4. Themes whose name starts with "Dark", with Neutral Dark right under Dark
 */
function buildThemeList(): ThemeListEntry[] {
  const system: ThemeListEntry = {
    id: 'system',
    name: 'System',
    kind: 'system',
    colors: THEMES.dark.colors,
  }
  const concrete = Object.values(THEMES)
  const light = concrete.filter((t) => t.id === 'light')
  // Neutral Dark sits with the "Dark*" block (directly under Dark), not the colored accents.
  const rest = concrete.filter((t) => t.id !== 'light' && t.id !== 'neutral')
  const notDarkName = rest.filter((t) => !t.name.startsWith('Dark'))
  const darkName = rest.filter((t) => t.name.startsWith('Dark'))
  const byName = (a: Theme, b: Theme) => a.name.localeCompare(b.name)
  notDarkName.sort(byName)
  darkName.sort(byName)
  // Dark → Neutral Dark → Dark Forest → Dark Purple …
  const darkBlock: Theme[] = []
  for (const t of darkName) {
    darkBlock.push(t)
    if (t.id === 'dark') darkBlock.push(THEMES.neutral)
  }
  return [system, ...light, ...notDarkName, ...darkBlock]
}

export const THEME_LIST: ThemeListEntry[] = buildThemeList()

export function systemPrefersLight(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches
  } catch {
    return false
  }
}

export function resolveThemeId(id: ThemeId): Exclude<ThemeId, 'system'> {
  if (id === 'system') {
    return systemPrefersLight() ? 'light' : 'dark'
  }
  return id
}

export function getResolvedTheme(id: ThemeId): Theme {
  const resolved = resolveThemeId(id)
  return THEMES[resolved]
}

export function applyTheme(theme: Theme, opts?: { customAccent?: string }): void {
  const root = document.documentElement
  // Brief CSS transition so theme switches feel polished (not jarring).
  root.classList.add('theme-animating')
  root.setAttribute('data-theme', theme.id)
  root.setAttribute('data-theme-kind', theme.kind)
  root.style.colorScheme = theme.kind
  // Apply CSS variables for components that read inline from THEMES
  for (const [k, v] of Object.entries(theme.colors)) {
    root.style.setProperty(k, v)
  }
  // Optional custom accent override (after palette so it wins).
  const ca = (opts?.customAccent || '').trim()
  if (ca && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(ca)) {
    const h = ca.replace('#', '')
    const full =
      h.length === 3
        ? h
            .split('')
            .map((c) => c + c)
            .join('')
        : h
    const n = parseInt(full, 16)
    const r = Math.max(0, ((n >> 16) & 255) - 18)
    const g = Math.max(0, ((n >> 8) & 255) - 18)
    const b = Math.max(0, (n & 255) - 18)
    const hover = `#${[r, g, b].map((x) => x.toString(16).padStart(2, '0')).join('')}`
    root.style.setProperty('--accent', ca)
    root.style.setProperty('--accent-hover', hover)
    root.style.setProperty('--chat-user-bg', ca)
    root.style.setProperty('--chat-user-border', ca)
  }
  window.setTimeout(() => root.classList.remove('theme-animating'), 260)
  void syncNativeWindowTheme(theme.kind)
}

async function syncNativeWindowTheme(kind: 'dark' | 'light'): Promise<void> {
  try {
    if (typeof window === 'undefined') return
    const w = window as Window & {
      __TAURI__?: unknown
      __TAURI_INTERNALS__?: unknown
    }
    if (!w.__TAURI__ && !w.__TAURI_INTERNALS__) return
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    await getCurrentWindow().setTheme(kind)
  } catch {
    // Browser / older runtime — ignore
  }
}
