/**
 * Workspace rail layout, browser open, popout, side swap.
 * Extracted from App.tsx.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  layoutOpenBrowserBesideSettings,
  layoutOpenBrowserInRail,
  loadWorkspaceLayout,
  saveWorkspaceLayout,
  type WorkspaceLayout,
} from '../workspace/layoutPrefs'
import type { SlideId } from '../workspace/types'
import { isTauri, tauriListen } from '../api/tauri'

export function useWorkspaceChrome(opts: {
  setPanel: (p: 'memory' | 'skills' | 'settings' | null) => void
}) {
  const { setPanel } = opts
  const [wsLayout, setWsLayout] = useState<WorkspaceLayout>(() => loadWorkspaceLayout())
  const [popout, setPopout] = useState<{
    id: SlideId
    fullscreen: boolean
  } | null>(null)

  const patchWs = useCallback((patch: Partial<WorkspaceLayout>) => {
    setWsLayout((prev) => {
      const next = { ...prev, ...patch }
      if (next.left === 'browser' && next.right === 'browser') {
        if (patch.left === 'browser') {
          next.right = prev.right === 'browser' ? 'files' : prev.right
        } else if (patch.right === 'browser') {
          next.left = prev.left === 'browser' ? 'files' : prev.left
        } else {
          next.right = 'files'
        }
      }
      saveWorkspaceLayout(next)
      return next
    })
  }, [])

  const resyncBrowserBounds = useCallback(() => {
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent('remedy:browser-resync-bounds'))
    }, 80)
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent('remedy:browser-resync-bounds'))
    }, 320)
  }, [])

  const openBrowserInRail = useCallback(() => {
    setPanel(null)
    setWsLayout((prev) => {
      const next = layoutOpenBrowserInRail(prev)
      saveWorkspaceLayout(next)
      return next
    })
    resyncBrowserBounds()
  }, [resyncBrowserBounds, setPanel])

  const openBrowserBesideSettings = useCallback(() => {
    setPanel(null)
    setWsLayout((prev) => {
      const next = layoutOpenBrowserBesideSettings(prev)
      saveWorkspaceLayout(next)
      return next
    })
    resyncBrowserBounds()
  }, [resyncBrowserBounds, setPanel])

  useEffect(() => {
    const onComputerUi = (ev: Event) => {
      const detail = (ev as CustomEvent<{ openBrowser?: boolean; keepSettings?: boolean }>).detail
      if (!detail?.openBrowser) return
      if (detail.keepSettings) openBrowserBesideSettings()
      else openBrowserInRail()
    }
    window.addEventListener('remedy:computer-ui', onComputerUi)
    let cancelled = false
    let unlisten: (() => void) | undefined
    void (async () => {
      if (!isTauri()) return
      try {
        const unlistenOpen = await tauriListen('computer-open-browser', (ev) => {
          openBrowserInRail()
          const payload = (ev as { payload?: { url?: string } })?.payload
          const u = payload?.url
          if (u) {
            window.dispatchEvent(
              new CustomEvent('remedy:browser-set-url', { detail: { url: u } }),
            )
          }
        })
        if (cancelled) {
          unlistenOpen?.()
          return
        }
        const unlistenUrl = await tauriListen('computer-browser-url', (ev) => {
          const payload = (ev as { payload?: { url?: string } })?.payload
          const u = payload?.url
          if (u) {
            window.dispatchEvent(
              new CustomEvent('remedy:browser-set-url', { detail: { url: u } }),
            )
          }
        })
        if (cancelled) {
          unlistenOpen?.()
          unlistenUrl?.()
          return
        }
        unlisten = () => {
          unlistenOpen?.()
          unlistenUrl?.()
        }
      } catch {
        /* older shell */
      }
    })()
    return () => {
      cancelled = true
      window.removeEventListener('remedy:computer-ui', onComputerUi)
      unlisten?.()
    }
  }, [openBrowserBesideSettings, openBrowserInRail])

  const swapSides = useCallback(() => {
    setWsLayout((prev) => {
      const next: WorkspaceLayout = {
        ...prev,
        left: prev.right,
        right: prev.left,
        leftWidth: prev.rightWidth,
        rightWidth: prev.leftWidth,
        leftOpen: prev.rightOpen,
        rightOpen: prev.leftOpen,
        leftRail: prev.rightRail,
        rightRail: prev.leftRail,
      }
      saveWorkspaceLayout(next)
      return next
    })
  }, [])

  return {
    wsLayout,
    setWsLayout,
    popout,
    setPopout,
    patchWs,
    openBrowserInRail,
    swapSides,
  }
}
