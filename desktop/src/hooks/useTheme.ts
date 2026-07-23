import { useState, useEffect, useCallback } from 'react'
import {
  type ThemeId,
  THEMES,
  THEME_LIST,
  applyTheme,
  getResolvedTheme,
  resolveThemeId,
} from '../themes'

const STORAGE_KEY = 'remedy-theme'

function loadTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'system') return 'system'
    if (stored && stored in THEMES) return stored as ThemeId
  } catch {
    // localStorage unavailable
  }
  return 'system'
}

function saveTheme(id: ThemeId): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // ignore
  }
}

export function useTheme() {
  const [themeId, setThemeId] = useState<ThemeId>(loadTheme)

  const applyResolved = useCallback((id: ThemeId) => {
    applyTheme(getResolvedTheme(id))
  }, [])

  useEffect(() => {
    applyResolved(themeId)
  }, [themeId, applyResolved])

  // Follow OS when theme is System
  useEffect(() => {
    if (themeId !== 'system') return
    let mq: MediaQueryList | null = null
    try {
      mq = window.matchMedia('(prefers-color-scheme: light)')
    } catch {
      return
    }
    const onChange = () => applyResolved('system')
    mq.addEventListener('change', onChange)
    return () => mq?.removeEventListener('change', onChange)
  }, [themeId, applyResolved])

  const set = useCallback((id: ThemeId) => {
    setThemeId(id)
    saveTheme(id)
  }, [])

  const resolvedId = resolveThemeId(themeId)
  const theme = THEMES[resolvedId]

  return { themeId, theme, resolvedId, set, themes: THEME_LIST }
}
