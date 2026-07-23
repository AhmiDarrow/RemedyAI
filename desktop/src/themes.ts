export type ThemeId = 'dark' | 'light' | 'green' | 'purple' | 'orange' | 'cyan'

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
}

export interface Theme {
  id: ThemeId
  name: string
  kind: 'dark' | 'light'
  colors: ThemeColors
}

export const THEMES: Record<ThemeId, Theme> = {
  dark: {
    id: 'dark',
    name: 'Dark',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a0a1a',
      '--bg-secondary': '#12122a',
      '--bg-tertiary': '#1a1a35',
      '--border': '#1e1e3e',
      '--accent': '#7c3aed',
      '--accent-hover': '#6d28d9',
      '--text-primary': '#e0e0e0',
      '--text-secondary': '#8888aa',
      '--text-muted': '#555577',
      '--success': '#22c55e',
      '--error': '#ef4444',
      '--warning': '#f59e0b',
    },
  },

  light: {
    id: 'light',
    name: 'Light',
    kind: 'light',
    colors: {
      '--bg-primary': '#f5f5f9',
      '--bg-secondary': '#ffffff',
      '--bg-tertiary': '#e8e8f0',
      '--border': '#d0d0da',
      '--accent': '#7c3aed',
      '--accent-hover': '#6d28d9',
      '--text-primary': '#1a1a2e',
      '--text-secondary': '#555566',
      '--text-muted': '#9999aa',
      '--success': '#16a34a',
      '--error': '#dc2626',
      '--warning': '#d97706',
    },
  },

  green: {
    id: 'green',
    name: 'Emerald',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a1a12',
      '--bg-secondary': '#122c1e',
      '--bg-tertiary': '#1a3d2a',
      '--border': '#1e4e36',
      '--accent': '#10b981',
      '--accent-hover': '#059669',
      '--text-primary': '#d1fae5',
      '--text-secondary': '#6ee7b7',
      '--text-muted': '#3b8268',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#fbbf24',
    },
  },

  purple: {
    id: 'purple',
    name: 'Amethyst',
    kind: 'dark',
    colors: {
      '--bg-primary': '#120a1a',
      '--bg-secondary': '#1e122a',
      '--bg-tertiary': '#2a1a3d',
      '--border': '#3e1e4e',
      '--accent': '#a855f7',
      '--accent-hover': '#9333ea',
      '--text-primary': '#ede9fe',
      '--text-secondary': '#a78bfa',
      '--text-muted': '#6b4c9a',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#facc15',
    },
  },

  orange: {
    id: 'orange',
    name: 'Amber',
    kind: 'dark',
    colors: {
      '--bg-primary': '#1a100a',
      '--bg-secondary': '#2c1e12',
      '--bg-tertiary': '#3d2a1a',
      '--border': '#4e361e',
      '--accent': '#f97316',
      '--accent-hover': '#ea580c',
      '--text-primary': '#ffedd5',
      '--text-secondary': '#d6a574',
      '--text-muted': '#8b6b4a',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#fbbf24',
    },
  },

  cyan: {
    id: 'cyan',
    name: 'Ocean',
    kind: 'dark',
    colors: {
      '--bg-primary': '#0a1a1e',
      '--bg-secondary': '#12262c',
      '--bg-tertiary': '#1a353d',
      '--border': '#1e464e',
      '--accent': '#06b6d4',
      '--accent-hover': '#0891b2',
      '--text-primary': '#cffafe',
      '--text-secondary': '#67e8f9',
      '--text-muted': '#3b7f8b',
      '--success': '#34d399',
      '--error': '#f87171',
      '--warning': '#facc15',
    },
  },
} as const

export const THEME_LIST = Object.values(THEMES)

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme.id)
  document.documentElement.setAttribute('data-theme-kind', theme.kind)
  // Keep OS/WebView chrome (and our custom titlebar contrast) in sync.
  document.documentElement.style.colorScheme = theme.kind
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
