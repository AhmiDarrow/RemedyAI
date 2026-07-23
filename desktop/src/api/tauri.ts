/** Tauri IPC helpers — prefer official @tauri-apps/api, fall back to globals. */

import { invoke as officialInvoke } from '@tauri-apps/api/core'
import { listen as officialListen } from '@tauri-apps/api/event'

/**
 * Detect Tauri shell. Tauri 2 often exposes `__TAURI_INTERNALS__` without
 * `__TAURI__` unless `withGlobalTauri` is on — both must count.
 */
export function isTauri(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as any
  return !!(
    w.__TAURI__
    || w.__TAURI_INTERNALS__
    || w.isTauri
    || (typeof navigator !== 'undefined' && (navigator as any).userAgent?.includes?.('Tauri'))
  )
}

function rawInvoke(): ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null {
  const w = window as any
  if (typeof w.__TAURI_INTERNALS__?.invoke === 'function') {
    return w.__TAURI_INTERNALS__.invoke.bind(w.__TAURI_INTERNALS__)
  }
  if (typeof w.__TAURI__?.core?.invoke === 'function') {
    return w.__TAURI__.core.invoke.bind(w.__TAURI__.core)
  }
  return null
}

export async function tauriInvoke<T = unknown>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  // Prefer official package when it works.
  try {
    return await officialInvoke<T>(cmd, args ?? {})
  } catch (e1) {
    const inv = rawInvoke()
    if (inv) {
      try {
        return (await inv(cmd, args ?? {})) as T
      } catch (e2) {
        throw e2 instanceof Error ? e2 : e1
      }
    }
    throw e1 instanceof Error ? e1 : new Error('Tauri bridge unavailable')
  }
}

/**
 * Listen for Tauri events. Returns an unlisten function.
 */
export async function tauriListen(
  event: string,
  handler: (payload: unknown) => void,
): Promise<() => void> {
  try {
    const unlisten = await officialListen(event, (e) => {
      handler(e.payload)
    })
    return unlisten
  } catch {
    // Fallback for older bridges
    try {
      const w = window as any
      const coreListen = w.__TAURI__?.event?.listen
      if (typeof coreListen === 'function') {
        const unlisten = await coreListen(event, (e: { payload?: unknown }) => {
          handler(e?.payload !== undefined ? e.payload : e)
        })
        return typeof unlisten === 'function' ? unlisten : () => {}
      }
    } catch {
      // ignore
    }
  }

  return () => {}
}
