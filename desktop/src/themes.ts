export type ThemeId = 'system' | 'dark' | 'light' | 'green' | 'purple' | 'orange' | 'cyan'

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
  '--chat-bubble-radius': '1rem',
  '--chat-max-width': '78%',
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

  light: {
    id: 'light',
    name: 'Light',
    kind: 'light',
    colors: {
      '--bg-primary': '#f4f4f8',
      '--bg-secondary': '#ffffff',
      '--bg-tertiary': '#eaeaef',
      '--border': '#c8c8d4',
      '--accent': '#7c3aed',
      '--accent-hover': '#6d28d9',
      '--text-primary': '#14141f',
      '--text-secondary': '#3d3d52',
      '--text-muted': '#5c5c72',
      '--success': '#15803d',
      '--error': '#b91c1c',
      '--warning': '#b45309',
      ...chatFromPalette('light', '#7c3aed', '#ffffff', '#eaeaef', '#c8c8d4', '#14141f', '#b91c1c'),
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
      '--text-primary': '#ecfdf5',
      '--text-secondary': '#a7f3d0',
      '--text-muted': '#6bb89a',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fcd34d',
      ...chatFromPalette('dark', '#10b981', '#0f1f18', '#163026', '#1f4a38', '#ecfdf5', '#fca5a5'),
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
      '--text-primary': '#f5f3ff',
      '--text-secondary': '#d8b4fe',
      '--text-muted': '#a78bfa',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#a855f7', '#1a1224', '#261a36', '#3d2a55', '#f5f3ff', '#fca5a5'),
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
      '--text-primary': '#fff7ed',
      '--text-secondary': '#fdba74',
      '--text-muted': '#c4a574',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#f97316', '#1f1810', '#2e2418', '#4a3a24', '#fff7ed', '#fca5a5'),
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
      '--text-primary': '#ecfeff',
      '--text-secondary': '#a5f3fc',
      '--text-muted': '#67c4d4',
      '--success': '#6ee7b7',
      '--error': '#fca5a5',
      '--warning': '#fde68a',
      ...chatFromPalette('dark', '#06b6d4', '#0f1c22', '#163038', '#1e4a55', '#ecfeff', '#fca5a5'),
    },
  },
} as const

/** UI list: System first, then concrete themes. */
export const THEME_LIST: { id: ThemeId; name: string; kind: 'dark' | 'light' | 'system'; colors: ThemeColors }[] = [
  {
    id: 'system',
    name: 'System',
    kind: 'system',
    colors: THEMES.dark.colors,
  },
  ...Object.values(THEMES),
]

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

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme.id)
  document.documentElement.setAttribute('data-theme-kind', theme.kind)
  document.documentElement.style.colorScheme = theme.kind
  // Apply CSS variables for components that read inline from THEMES
  for (const [k, v] of Object.entries(theme.colors)) {
    document.documentElement.style.setProperty(k, v)
  }
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
