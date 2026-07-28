import { useCallback, useEffect, useRef, useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { getSettings } from '../../api/settings'
import {
  DEFAULT_BROWSER_HOME,
  normalizeBrowserUrl,
  resolveBrowserHome,
} from '../../utils/browserUrl'
import { openExternalUrl } from '../../api/auth'

type Bounds = { x: number; y: number; width: number; height: number }

/**
 * CSS-pixel bounds for the native WebView2 child.
 * Only report a usable host so the embed never covers PopoutOverlay chrome
 * or zero-size rail leftovers.
 */
function readBounds(el: HTMLElement | null): Bounds | null {
  if (!el || !el.isConnected) return null
  const style = window.getComputedStyle(el)
  if (
    style.display === 'none'
    || style.visibility === 'hidden'
    || Number(style.opacity) === 0
  ) {
    return null
  }
  const r = el.getBoundingClientRect()
  // Require a real panel — tiny/offscreen rects fight fullscreen popout bounds
  if (r.width < 80 || r.height < 80) return null
  if (r.bottom < 0 || r.right < 0) return null
  if (r.top > window.innerHeight || r.left > window.innerWidth) return null
  // Inset 1px so chrome borders don't clip WebView2
  return {
    x: Math.round(r.left) + 1,
    y: Math.round(r.top) + 1,
    width: Math.max(80, Math.round(r.width) - 2),
    height: Math.max(80, Math.round(r.height) - 2),
  }
}

/** Delay hide so rail↔popout remounts do not flash-hide the native child. */
let embedMountCount = 0
let hideTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Embedded WebView2 (Chromium) browser **inside** the Browser slide.
 * Separate OS popup only when the user clicks ↗ (system browser).
 */
export function BrowserSlide() {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [home, setHome] = useState(DEFAULT_BROWSER_HOME)
  const [url, setUrl] = useState(DEFAULT_BROWSER_HOME)
  const [activeUrl, setActiveUrl] = useState(DEFAULT_BROWSER_HOME)
  const [status, setStatus] = useState('Loading homepage…')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const autoStarted = useRef(false)
  const goRef = useRef<(raw: string) => Promise<void>>(async () => {})

  // Settings → browser_home_url (default: Remedy GitHub)
  useEffect(() => {
    let cancelled = false
    void getSettings()
      .then((s) => {
        if (cancelled) return
        const h = resolveBrowserHome(s.browser_home_url)
        setHome(h)
        // Only seed the address bar when still on the previous default / empty
        setUrl((prev) => {
          const p = (prev || '').trim()
          if (!p || p === DEFAULT_BROWSER_HOME || p === 'https://example.com') return h
          return prev
        })
        setActiveUrl((prev) => {
          const p = (prev || '').trim()
          if (!p || p === DEFAULT_BROWSER_HOME || p === 'https://example.com') return h
          return prev
        })
      })
      .catch(() => {
        /* keep DEFAULT_BROWSER_HOME */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const pushBounds = useCallback(async () => {
    if (!isTauri() || !loaded) return
    const b = readBounds(hostRef.current)
    if (!b) return
    try {
      await tauriInvoke('browser_set_bounds', { bounds: b })
    } catch {
      /* not open yet */
    }
  }, [loaded])

  // Keep child webview aligned with the host while the slide is open
  useEffect(() => {
    const el = hostRef.current
    if (!el || !isTauri()) return
    const ro = new ResizeObserver(() => {
      void pushBounds()
    })
    ro.observe(el)
    const onWin = () => void pushBounds()
    window.addEventListener('resize', onWin)
    // Agent/openBrowserInRail asks for an immediate bounds push
    const onResync = () => {
      void pushBounds()
      // A few frames later — layout may still be settling
      window.requestAnimationFrame(() => void pushBounds())
      window.setTimeout(() => void pushBounds(), 50)
      window.setTimeout(() => void pushBounds(), 200)
    }
    window.addEventListener('remedy:browser-resync-bounds', onResync)
    // Popout/fullscreen layout can settle over several frames
    let n = 0
    let raf = 0
    const tick = () => {
      void pushBounds()
      n += 1
      if (n < 20) raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', onWin)
      window.removeEventListener('remedy:browser-resync-bounds', onResync)
      window.cancelAnimationFrame(raf)
    }
  }, [pushBounds])

  // Track mounts so hide is deferred across remount (popout / StrictMode).
  useEffect(() => {
    if (!isTauri()) return
    embedMountCount += 1
    if (hideTimer) {
      clearTimeout(hideTimer)
      hideTimer = null
    }
    return () => {
      embedMountCount = Math.max(0, embedMountCount - 1)
      if (hideTimer) clearTimeout(hideTimer)
      hideTimer = setTimeout(() => {
        hideTimer = null
        if (embedMountCount === 0) {
          void tauriInvoke('browser_hide').catch(() => {})
        }
      }, 80)
    }
  }, [])

  // Show again if still open when remounting (e.g. popout open/close)
  useEffect(() => {
    if (!isTauri()) return
    let cancelled = false
    void (async () => {
      try {
        const open = await tauriInvoke<boolean>('browser_is_open')
        if (cancelled) return
        if (open) {
          setLoaded(true)
          await tauriInvoke('browser_show').catch(() => {})
          await new Promise<void>((r) => requestAnimationFrame(() => r()))
          await pushBounds()
          await new Promise<void>((r) => requestAnimationFrame(() => r()))
          await pushBounds()
          setStatus('Browser ready')
          autoStarted.current = true
        }
      } catch {
        /* */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [pushBounds])

  const go = useCallback(
    async (raw: string) => {
      const u = normalizeBrowserUrl(raw)
      if (!u) {
        setStatus('Enter an http(s) URL')
        return
      }
      setUrl(u)
      setActiveUrl(u)
      if (!isTauri()) {
        // Web UI: iframe is best-effort (many sites block framing)
        setLoaded(true)
        setStatus('Web UI iframe (desktop embeds WebView2)')
        return
      }
      // Wait for layout if the rail just opened (bounds often 0 for a frame).
      let b = readBounds(hostRef.current)
      if (!b) {
        for (let i = 0; i < 16 && !b; i++) {
          await new Promise<void>((r) => requestAnimationFrame(() => r()))
          b = readBounds(hostRef.current)
        }
      }
      if (!b) {
        setStatus('Browser panel too small — expand the Browser rail, then press Go')
        return
      }
      setBusy(true)
      setStatus('Loading…')
      try {
        const nav = await tauriInvoke<string>('browser_navigate', {
          url: u,
          bounds: b,
        })
        setActiveUrl(nav || u)
        setLoaded(true)
        setStatus('Loaded in Remedy (WebView2)')
        const resync = () => void pushBounds()
        window.requestAnimationFrame(resync)
        window.setTimeout(resync, 50)
        window.setTimeout(resync, 200)
        window.setTimeout(resync, 500)
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        console.warn('[remedy] browser_navigate failed', msg)
        setStatus(`Embed failed: ${msg}`)
        setLoaded(false)
      } finally {
        setBusy(false)
      }
    },
    [pushBounds],
  )
  goRef.current = go

  // Auto-load homepage once the host has a real size (desktop only).
  useEffect(() => {
    if (!isTauri() || autoStarted.current) return
    let cancelled = false
    let attempts = 0
    const tick = () => {
      if (cancelled || autoStarted.current) return
      attempts += 1
      const b = readBounds(hostRef.current)
      if (b) {
        autoStarted.current = true
        void goRef.current(home)
        return
      }
      if (attempts < 40) {
        window.setTimeout(tick, 50)
      } else {
        setStatus('Expand Browser rail, then press Go (or ↗ for system browser)')
      }
    }
    // Defer one frame so flex layout can measure
    const id = window.requestAnimationFrame(() => tick())
    return () => {
      cancelled = true
      window.cancelAnimationFrame(id)
    }
  }, [home])

  const closeEmbed = useCallback(async () => {
    if (!isTauri()) {
      setLoaded(false)
      setStatus('Closed')
      return
    }
    try {
      await tauriInvoke('browser_close')
      setLoaded(false)
      autoStarted.current = false
      setStatus('Browser closed')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const openExternal = async () => {
    const u = normalizeBrowserUrl(url) || activeUrl
    try {
      if (isTauri()) {
        try {
          await openExternalUrl(u)
        } catch {
          await tauriInvoke('open_external_url', { url: u })
        }
        setStatus('Opened in system browser (popup)')
        return
      }
      window.open(u, '_blank', 'noopener,noreferrer')
      setStatus('Opened in browser tab')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 max-h-full overflow-hidden text-xs">
      {/* React toolbar stays above native WebView2 (hostRef only below this) */}
      <form
        className="flex gap-1 px-2 py-1.5 border-b shrink-0"
        style={{
          borderColor: 'var(--border)',
          background: 'var(--bg-secondary)',
          position: 'relative',
          zIndex: 2,
        }}
        onSubmit={(e) => {
          e.preventDefault()
          void go(url)
        }}
      >
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title={`Home (${home})`}
          onClick={() => void go(home)}
        >
          ⌂
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Reload"
          disabled={!loaded || busy}
          onClick={() => {
            if (!isTauri() || !loaded) return
            void tauriInvoke('browser_reload')
              .then(() => setStatus('Reloaded'))
              .catch(() => void go(activeUrl))
          }}
        >
          ↻
        </button>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="flex-1 min-w-0 rounded px-1.5 py-1 outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          placeholder="https://"
          aria-label="Browser URL"
          spellCheck={false}
        />
        <button
          type="submit"
          className="px-2 py-1 rounded font-medium"
          style={{ background: 'var(--accent)', color: '#fff' }}
          disabled={busy}
        >
          Go
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Open in system browser (external popup)"
          onClick={() => void openExternal()}
        >
          ↗
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{
            border: '1px solid var(--border)',
            color: loaded ? 'var(--error)' : 'var(--text-muted)',
            opacity: loaded ? 1 : 0.5,
          }}
          title="Close embedded browser"
          disabled={!loaded}
          onClick={() => void closeEmbed()}
        >
          ✕
        </button>
      </form>

      {/* Host rect ONLY: native WebView2 is positioned over this box — never the popout chrome */}
      <div
        ref={hostRef}
        className="flex-1 min-h-0 relative w-full overflow-hidden"
        style={{
          background: loaded ? '#ffffff' : 'var(--bg-primary)',
          minHeight: 120,
          zIndex: 1,
          isolation: 'isolate',
        }}
        data-browser-embed-host
      >
        {!loaded && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center"
            style={{ color: 'var(--text-secondary)' }}
          >
            <div className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              Embedded browser
            </div>
            <p className="max-w-sm text-[11px] leading-relaxed">
              WebView2 loads inside this panel automatically. Press <strong>Go</strong> to
              retry, or <strong>↗</strong> for the system browser.
            </p>
            {busy && (
              <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                Loading…
              </p>
            )}
          </div>
        )}
        {/* Desktop: native child webview paints here. Web UI fallback: iframe. */}
        {!isTauri() && loaded && (
          <iframe
            title="Remedy Browser"
            src={activeUrl}
            className="absolute inset-0 w-full h-full border-0"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          />
        )}
      </div>

      {status && (
        <div
          className="px-2 py-1 border-t truncate shrink-0"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          title={status}
        >
          {status}
        </div>
      )}
    </div>
  )
}
