import { useState, useEffect, useCallback } from 'react'
import { type ThemeId, THEMES, applyTheme } from '../themes'

const STORAGE_KEY = 'remedy-theme'

function loadTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && stored in THEMES) return stored as ThemeId
  } catch {
    // localStorage unavailable
  }
  return 'dark'
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

  useEffect(() => {
    applyTheme(THEMES[themeId])
  }, [themeId])

  const set = useCallback((id: ThemeId) => {
    setThemeId(id)
    saveTheme(id)
  }, [])

  const theme = THEMES[themeId]

  return { themeId, theme, set, themes: THEMES }
}
