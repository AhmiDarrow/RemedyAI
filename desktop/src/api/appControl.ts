/** App control — poll for UI actions Remedy enqueued (switch surface, etc.).
 *
 * Mirrors the browser ui_command channel but for Remedy's OWN interface:
 * she does things "within herself" instantly instead of asking the user to
 * click. The server holds a tiny FIFO; we take one command per poll and
 * dispatch it.
 */

import { apiFetch } from './client'

export type AppCommand = {
  id: string
  action:
    | 'switch_surface'
    | 'open_goal'
    | 'focus_composer'
    | 'open_settings'
    | 'open_panel'
    | 'close_ui'
    | 'new_session'
  params?: {
    target?: 'grove' | 'studio' | 'alongside' | 'storyline' | 'home'
    goal_id?: string
    section?: string
    panel?: string
    article?: string
  }
  ts?: number
}

/** Take (and remove) the next queued app command, or null. */
export async function takeAppCommand(): Promise<AppCommand | null> {
  try {
    const r = await apiFetch<{ command: AppCommand | null }>(
      '/app/command?take=1',
    )
    return r.command ?? null
  } catch {
    return null
  }
}
