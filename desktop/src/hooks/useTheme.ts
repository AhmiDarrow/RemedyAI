import { useState, useEffect, useCallback } from 'react'
import {
  type ThemeId,
  THEMES,
  THEME_LIST,
  DEFAULT_THEME_ID,
  applyTheme,
  getResolvedTheme,
  resolveThemeId,
  isThemeId,
} from '../themes'
import {
  type Density,
  type FontScale,
  loadDensity,
  saveDensity,
  applyDensity,
  loadCustomAccent,
  saveCustomAccent,
  loadFontScale,
  saveFontScale,
  applyFontScale,
  stepFontScale,
  loadReduceMotion,
  saveReduceMotion,
  applyReduceMotion,
  loadHighContrast,
  saveHighContrast,
  applyHighContrast,
} from '../utils/chatPrefs'

const STORAGE_KEY = 'remedy-theme'

function loadTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    // Honor a previous explicit choice only when it is a known id.
    if (isThemeId(stored)) return stored
  } catch {
    // localStorage unavailable
  }
  // First install / first run / corrupt key: always Dark Forest.
  try {
    localStorage.setItem(STORAGE_KEY, DEFAULT_THEME_ID)
  } catch {
    // ignore
  }
  return DEFAULT_THEME_ID
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
  const [density, setDensityState] = useState<Density>(loadDensity)
  const [customAccent, setCustomAccentState] = useState(loadCustomAccent)
  const [fontScale, setFontScaleState] = useState<FontScale>(loadFontScale)
  const [reduceMotion, setReduceMotionState] = useState(loadReduceMotion)
  const [highContrast, setHighContrastState] = useState(loadHighContrast)

  const applyResolved = useCallback(
    (id: ThemeId, accent = customAccent) => {
      applyTheme(getResolvedTheme(id), { customAccent: accent })
    },
    [customAccent],
  )

  useEffect(() => {
    applyResolved(themeId)
  }, [themeId, applyResolved])

  useEffect(() => {
    applyDensity(density)
  }, [density])

  useEffect(() => {
    applyFontScale(fontScale)
  }, [fontScale])

  useEffect(() => {
    applyReduceMotion(reduceMotion)
  }, [reduceMotion])

  useEffect(() => {
    applyHighContrast(highContrast)
  }, [highContrast])

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

  const setDensity = useCallback((d: Density) => {
    setDensityState(d)
    saveDensity(d)
    applyDensity(d)
  }, [])

  const setFontScale = useCallback((s: FontScale) => {
    setFontScaleState(s)
    saveFontScale(s)
    applyFontScale(s)
  }, [])

  const bumpFontScale = useCallback((dir: 1 | -1) => {
    setFontScaleState((cur) => {
      const next = stepFontScale(cur, dir)
      saveFontScale(next)
      applyFontScale(next)
      return next
    })
  }, [])

  const setReduceMotion = useCallback((on: boolean) => {
    setReduceMotionState(on)
    saveReduceMotion(on)
    applyReduceMotion(on)
  }, [])

  const setHighContrast = useCallback((on: boolean) => {
    setHighContrastState(on)
    saveHighContrast(on)
    applyHighContrast(on)
  }, [])

  const setCustomAccent = useCallback(
    (hex: string) => {
      const v = hex.trim()
      setCustomAccentState(v)
      saveCustomAccent(v)
      applyResolved(themeId, v)
    },
    [themeId, applyResolved],
  )

  const resolvedId = resolveThemeId(themeId)
  const theme = THEMES[resolvedId]

  return {
    themeId,
    theme,
    resolvedId,
    set,
    themes: THEME_LIST,
    density,
    setDensity,
    customAccent,
    setCustomAccent,
    fontScale,
    setFontScale,
    bumpFontScale,
    reduceMotion,
    setReduceMotion,
    highContrast,
    setHighContrast,
  }
}
