/** Top-level surface: Grove (partner home, default) vs Studio (full workbench).
 *
 * Grove is the default for new owners (docs/LIFE_TASK_PARTNER.md); Studio is
 * one tap away and the choice persists. Distinct from UiMode (simple/advanced),
 * which only tunes Studio's chrome density.
 */

export type AppSurface = 'grove' | 'studio'

const KEY = 'remedy.surface'

export function loadSurface(): AppSurface {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'grove' || v === 'studio') return v
  } catch {
    /* */
  }
  return 'grove'
}

export function saveSurface(surface: AppSurface): void {
  try {
    localStorage.setItem(KEY, surface)
  } catch {
    /* */
  }
}
