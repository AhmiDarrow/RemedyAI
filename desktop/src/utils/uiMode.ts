/** App chrome: Simple (few controls) vs Advanced (full bottom bar). */

export type UiMode = 'simple' | 'advanced'

const KEY = 'remedy.uiMode'

export function loadUiMode(): UiMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'simple' || v === 'advanced') return v
  } catch {
    /* */
  }
  return 'simple'
}

export function saveUiMode(mode: UiMode): void {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* */
  }
}
