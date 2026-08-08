/** Settings surface: Simple (defaults, few choices) vs Advanced (full control). */

export type SettingsMode = 'simple' | 'advanced'

const KEY = 'remedy.settingsMode'

export function loadSettingsMode(): SettingsMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'simple' || v === 'advanced') return v
  } catch {
    /* */
  }
  return 'simple'
}

export function saveSettingsMode(mode: SettingsMode): void {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* */
  }
}

/** Sections shown only in Advanced mode (ids match SettingsSectionId). */
export const ADVANCED_ONLY_SECTIONS = new Set([
  'provider-catalog',
  'access',
  'security-power',
  'tool-process',
  'vision', // image VLM — advanced; RMB chat host is Simple
  'memory-harness',
  'advanced',
  'mcp',
  'always-ready',
  'license',
])

export function isSectionVisibleInMode(id: string, mode: SettingsMode): boolean {
  if (mode === 'advanced') return true
  return !ADVANCED_ONLY_SECTIONS.has(id)
}
