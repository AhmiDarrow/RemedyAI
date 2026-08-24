import { useCallback, useEffect, useRef, useState } from 'react'
import { isTauri, tauriInvoke, tauriListen } from '../../api/tauri'
import { getSettings } from '../../api/settings'
import {
  DEFAULT_BROWSER_HOME,
  normalizeBrowserUrl,
  resolveBrowserAddressBar,
  resolveBrowserHome,
} from '../../utils/browserUrl'
import {
  isBookmarked,
  loadBookmarks,
  toggleBookmark,
  type BrowserBookmark,
} from '../../utils/browserBookmarks'
import { openExternalUrl } from '../../api/auth'
import {
  browserStackProbeHostCoverage,
  browserStackSetHostVisible,
  browserStackHold,
} from '../../utils/browserStack'
import { takePendingBrowserUrl } from '../../workspace/railNav'

type Bounds = { x: number; y: number; width: number; height: number }

/** Keep native embed below app title / popout chrome and above status bar. */
function chromeSafeBand(
  hostRect: DOMRect,
  opts?: { pageFullscreen?: boolean },
): { minY: number; maxBottom: number } {
  let minY = 0
  let maxBottom = window.innerHeight
  const pageFs = Boolean(opts?.pageFullscreen)

  const title = document.querySelector('.titlebar')
  if (title) {
    minY = Math.max(minY, title.getBoundingClientRect().bottom)
  } else {
    // Custom title strip is 36px when present
    minY = Math.max(minY, 36)
  }

  const popChrome = document.querySelector('[data-popout-chrome]')
  if (popChrome && !pageFs) {
    const pr = popChrome.getBoundingClientRect()
    if (pr.height > 8 && pr.bottom > minY) {
      minY = Math.max(minY, pr.bottom)
    }
  }

  // During page/video fullscreen, fill the whole Browser *rail panel*
  // (ignore URL toolbar + panel header — SPA hides them).
  if (!pageFs) {
    // URL toolbar for *this* browser panel (horizontal overlap with host)
    for (const tb of document.querySelectorAll('[data-browser-toolbar]')) {
      const tr = tb.getBoundingClientRect()
      if (tr.height < 4) continue
      if (tr.right < hostRect.left + 4 || tr.left > hostRect.right - 4) continue
      minY = Math.max(minY, tr.bottom)
    }

    // Panel "Browser" header row (WorkspaceSide) when it sits above the host
    for (const hd of document.querySelectorAll('[data-workspace-panel-header]')) {
      const hr = hd.getBoundingClientRect()
      if (hr.height < 4) continue
      if (hr.right < hostRect.left + 4 || hr.left > hostRect.right - 4) continue
      if (hr.bottom <= hostRect.top + 2 || hr.top < hostRect.top) {
        minY = Math.max(minY, hr.bottom)
      }
    }
  }

  const status = document.querySelector('[data-remedy-status-bar]')
  if (status) {
    maxBottom = Math.min(maxBottom, status.getBoundingClientRect().top)
  }

  // Browser slide status strip under the host (hidden in page fullscreen)
  if (!pageFs) {
    for (const st of document.querySelectorAll('[data-browser-status]')) {
      const sr = st.getBoundingClientRect()
      if (sr.height < 4) continue
      if (sr.right < hostRect.left + 4 || sr.left > hostRect.right - 4) continue
      if (sr.top >= hostRect.top) {
        maxBottom = Math.min(maxBottom, sr.top)
      }
    }
  }

  return { minY, maxBottom }
}

/**
 * CSS-pixel bounds for the native WebView2 child.
 * Clamped so the embed never covers title bar, panel header, or URL toolbar.
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

  const pageFullscreen =
    el.getAttribute('data-page-fullscreen') === '1'
    || el.closest('[data-page-fullscreen="1"]') != null
  const { minY, maxBottom } = chromeSafeBand(r, { pageFullscreen })
  // Fullscreen in rail: prefer the host's full box (SPA already hid chrome).
  // Still clamp to titlebar + app status bar so we never cover the whole app.
  const x = Math.round(r.left)
  const y = Math.round(pageFullscreen ? Math.max(r.top, minY) : Math.max(r.top, minY))
  const right = Math.round(r.right)
  const bottom = Math.round(
    pageFullscreen ? Math.min(r.bottom, maxBottom) : Math.min(r.bottom, maxBottom),
  )
  const width = Math.max(0, right - x)
  const height = Math.max(0, bottom - y)
  if (width < 80 || height < 80) return null

  return { x, y, width, height }
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
  /** True while user is editing the address bar — do not clobber with live URL. */
  const urlEditing = useRef(false)
  const statusTimer = useRef<number | null>(null)
  const [bookmarks, setBookmarks] = useState<BrowserBookmark[]>(() => loadBookmarks())
  const [bmOpen, setBmOpen] = useState(false)
  const bmPanelRef = useRef<HTMLDivElement | null>(null)
  const [shieldOn, setShieldOn] = useState(true)
  /** false = mobile UA (default, better in narrow rail); true = desktop site */
  const [desktopSite, setDesktopSite] = useState(false)
  /**
   * HTML/video fullscreen: hide rail chrome so the embed host fills *only* the
   * Browser panel (not the whole app). Rust keeps WebView2 bounds = host rect.
   */
  const [pageFullscreen, setPageFullscreen] = useState(false)

  /** Transient status that auto-clears (sticky “ready” messages stay briefly). */
  const flashStatus = useCallback((msg: string, ms = 3200) => {
    setStatus(msg)
    if (statusTimer.current != null) {
      window.clearTimeout(statusTimer.current)
      statusTimer.current = null
    }
    if (!msg) return
    // Keep errors longer; short-lived confirmations fade
    const hold =
      /fail|error|blocked|too small|not available/i.test(msg) ? Math.max(ms, 5000) : ms
    statusTimer.current = window.setTimeout(() => {
      statusTimer.current = null
      setStatus((cur) => (cur === msg ? '' : cur))
    }, hold)
  }, [])

  useEffect(() => {
    return () => {
      if (statusTimer.current != null) window.clearTimeout(statusTimer.current)
    }
  }, [])

  // Privacy Shield status (desktop)
  useEffect(() => {
    if (!isTauri()) return
    let cancelled = false
    void tauriInvoke<{ enabled?: boolean }>('privacy_shield_status')
      .then((s) => {
        if (!cancelled) setShieldOn(Boolean(s?.enabled))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Mobile / desktop view mode (persisted in ~/.remedy/browser_rail.json)
  useEffect(() => {
    if (!isTauri()) return
    let cancelled = false
    void tauriInvoke<{ desktop_site?: boolean }>('browser_view_mode')
      .then((s) => {
        if (!cancelled) setDesktopSite(Boolean(s?.desktop_site))
      })
      .catch(() => {})
    let unlisten: (() => void) | undefined
    void tauriListen<{ desktop_site?: boolean }>('browser-view-mode', (payload) => {
      setDesktopSite(Boolean(payload?.desktop_site))
    })
      .then((u) => {
        unlisten = u
      })
      .catch(() => {})
    return () => {
      cancelled = true
      unlisten?.()
    }
  }, [])

  // Video/HTML fullscreen → fill Browser rail only (hide toolbar/status, grow host)
  useEffect(() => {
    if (!isTauri()) return
    let unlisten: (() => void) | undefined
    void tauriListen<{ fullscreen?: boolean }>('browser-page-fullscreen', (payload) => {
      const on = Boolean(payload?.fullscreen)
      setPageFullscreen(on)
      if (on) {
        setStatus('Fullscreen in Browser rail (Esc to exit)')
      } else {
        setStatus('Browser ready')
      }
      // Layout settles after chrome hide/show — push host bounds into WebView2
      const kick = () => {
        window.dispatchEvent(new Event('remedy:browser-resync-bounds'))
      }
      window.requestAnimationFrame(kick)
      window.setTimeout(kick, 30)
      window.setTimeout(kick, 120)
      window.setTimeout(kick, 280)
    })
      .then((u) => {
        unlisten = u
      })
      .catch(() => {})
    return () => {
      unlisten?.()
    }
  }, [])

  // Esc while rail chrome is hidden: force-exit UI fullscreen if page already exited
  useEffect(() => {
    if (!pageFullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // WebView2 usually exits first and emits fullscreen=false; this is a safety net.
      window.setTimeout(() => {
        setPageFullscreen((cur) => {
          if (!cur) return cur
          setStatus('Browser ready')
          window.dispatchEvent(new Event('remedy:browser-resync-bounds'))
          return false
        })
      }, 50)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pageFullscreen])

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
    const host = hostRef.current
    const b = readBounds(host)
    // No usable host → suppress native HWND so it cannot float over chrome
    browserStackSetHostVisible(Boolean(b))
    browserStackProbeHostCoverage(host)
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
    // After stack unsuppress, re-clamp to the live host rect (not stale defaults)
    let unlistenRestored: (() => void) | undefined
    void tauriListen('browser-stack-restored', () => {
      void pushBounds()
      window.requestAnimationFrame(() => void pushBounds())
    })
      .then((u) => {
        unlistenRestored = u
      })
      .catch(() => {
        /* web / no tauri */
      })
    // Native HWND ignores CSS stacking — hide when host is off-screen / covered
    const io = new IntersectionObserver(
      (entries) => {
        const e = entries[0]
        const visible = Boolean(
          e
          && e.isIntersecting
          && e.intersectionRatio > 0.02
          && readBounds(el),
        )
        browserStackSetHostVisible(visible)
        if (visible) void pushBounds()
      },
      { threshold: [0, 0.02, 0.1, 0.5, 1], root: null },
    )
    io.observe(el)
    // Catch theme menus / overlays that open over the host (no React wiring needed)
    const coverIv = window.setInterval(() => {
      browserStackProbeHostCoverage(el)
      void pushBounds()
    }, 200)
    // Status-bar selects / Theme menu portal to <body> and open upward over
    // the host — probe on mount so the native child hides before it paints
    // over the menu (the interval alone leaves a ~200ms covered flash).
    const portalMo = new MutationObserver(() => browserStackProbeHostCoverage(el))
    portalMo.observe(document.body, { childList: true })
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
      io.disconnect()
      unlistenRestored?.()
      window.clearInterval(coverIv)
      portalMo.disconnect()
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
      browserStackSetHostVisible(false)
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
      // Omnibox: real URL if possible, else DuckDuckGo search
      const u = resolveBrowserAddressBar(raw)
      if (!u) {
        flashStatus('Enter a URL or search', 2500)
        return
      }
      urlEditing.current = false
      setUrl(u)
      setActiveUrl(u)
      if (!isTauri()) {
        // Web UI: iframe is best-effort (many sites block framing)
        setLoaded(true)
        flashStatus('Web UI iframe (desktop embeds WebView2)', 4000)
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
        flashStatus('Browser panel too small — expand the Browser rail, then press Go', 6000)
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
        flashStatus('Loaded', 2200)
        const resync = () => void pushBounds()
        window.requestAnimationFrame(resync)
        window.setTimeout(resync, 50)
        window.setTimeout(resync, 200)
        window.setTimeout(resync, 500)
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        console.warn('[remedy] browser_navigate failed', msg)
        flashStatus(`Embed failed: ${msg}`, 6000)
        setLoaded(false)
      } finally {
        setBusy(false)
      }
    },
    [pushBounds, flashStatus],
  )
  goRef.current = go

  const applyLiveUrl = useCallback((raw: string) => {
    const u = (raw || '').trim()
    if (!u || u.startsWith('about:')) return
    setActiveUrl(u)
    // Only rewrite the address bar when the user is not typing a new URL
    if (!urlEditing.current) {
      setUrl(u)
    }
  }, [])

  // Agent / chat double-click: set URL and navigate the embed (not address-bar only).
  useEffect(() => {
    const goTo = (u: string, navigate = true) => {
      applyLiveUrl(u)
      autoStarted.current = true
      if (!navigate) {
        setLoaded(true)
        setStatus(`URL ${u}`)
        void pushBounds()
        return
      }
      void goRef.current(u)
    }
    const pending = takePendingBrowserUrl()
    if (pending) goTo(pending)
    const onSetUrl = (ev: Event) => {
      const detail = (ev as CustomEvent<{ url?: string; navigate?: boolean }>).detail
      const u = (detail?.url || '').trim()
      if (!u) return
      takePendingBrowserUrl()
      goTo(u, detail?.navigate !== false)
    }
    window.addEventListener('remedy:browser-set-url', onSetUrl)
    return () => window.removeEventListener('remedy:browser-set-url', onSetUrl)
  }, [pushBounds, applyLiveUrl])

  // Live URL from WebView2 (page load + poll). Old code only stored last navigate target.
  useEffect(() => {
    if (!isTauri() || !loaded) return
    let cancelled = false
    const tick = async () => {
      if (cancelled) return
      try {
        const cur = await tauriInvoke<string>('browser_current_url')
        if (cur) applyLiveUrl(cur)
      } catch {
        /* embed closed */
      }
    }
    void tick()
    const iv = window.setInterval(() => void tick(), 800)
    let unlisten: (() => void) | undefined
    void tauriListen('browser-url-changed', (payload) => {
      const u = (payload as { url?: string } | null)?.url
      if (u) applyLiveUrl(u)
    })
      .then((u) => {
        unlisten = u
      })
      .catch(() => {})
    return () => {
      cancelled = true
      window.clearInterval(iv)
      unlisten?.()
    }
  }, [loaded, applyLiveUrl])

  // Bookmarks dropdown: close on outside click; suppress embed while open
  useEffect(() => {
    if (!bmOpen) return
    const release = browserStackHold('bookmarks-menu')
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (bmPanelRef.current?.contains(t)) return
      setBmOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => {
      release()
      document.removeEventListener('mousedown', onDoc)
    }
  }, [bmOpen])

  // Auto-load homepage only if embed is not already open (side-switch remount).
  useEffect(() => {
    if (!isTauri() || autoStarted.current) return
    let cancelled = false
    let attempts = 0
    let retryTimer: number | null = null
    const tick = async () => {
      if (cancelled || autoStarted.current) return
      attempts += 1
      try {
        const open = await tauriInvoke<boolean>('browser_is_open')
        if (open) {
          // Side switch remount: keep existing page, sync URL bar — do NOT load home
          autoStarted.current = true
          setLoaded(true)
          try {
            const cur = await tauriInvoke<string>('browser_current_url')
            if (cur && !cur.startsWith('about:')) {
              setUrl(cur)
              setActiveUrl(cur)
              flashStatus('Restored previous page', 2200)
            } else {
              flashStatus('Browser ready', 2000)
            }
          } catch {
            flashStatus('Browser ready', 2000)
          }
          await pushBounds()
          await tauriInvoke('browser_show').catch(() => {})
          return
        }
      } catch {
        /* */
      }
      if (cancelled) return
      const b = readBounds(hostRef.current)
      if (b) {
        autoStarted.current = true
        void goRef.current(home)
        return
      }
      if (attempts < 40) {
        retryTimer = window.setTimeout(() => void tick(), 50)
      } else {
        flashStatus('Expand Browser rail, then press Go (or ↗ for system browser)', 8000)
      }
    }
    const id = window.requestAnimationFrame(() => void tick())
    return () => {
      cancelled = true
      window.cancelAnimationFrame(id)
      if (retryTimer != null) window.clearTimeout(retryTimer)
    }
  }, [home, pushBounds, flashStatus])

  const closeEmbed = useCallback(async () => {
    if (!isTauri()) {
      setLoaded(false)
      flashStatus('Closed', 2000)
      return
    }
    try {
      await tauriInvoke('browser_close')
      setLoaded(false)
      autoStarted.current = false
      flashStatus('Browser closed', 2500)
    } catch (e: unknown) {
      flashStatus(e instanceof Error ? e.message : String(e), 5000)
    }
  }, [flashStatus])

  const openExternal = async () => {
    const u = normalizeBrowserUrl(url) || activeUrl
    if (!u) {
      flashStatus('Nothing to open — enter a URL first', 2500)
      return
    }
    try {
      if (isTauri()) {
        try {
          await openExternalUrl(u)
        } catch {
          await tauriInvoke('open_external_url', { url: u })
        }
        flashStatus('Opened in system browser', 2500)
        return
      }
      window.open(u, '_blank', 'noopener,noreferrer')
      flashStatus('Opened in browser tab', 2500)
    } catch (e: unknown) {
      flashStatus(e instanceof Error ? e.message : String(e), 5000)
    }
  }

  return (
    <div
      className="flex flex-col h-full min-h-0 max-h-full overflow-hidden text-xs"
      data-browser-rail-root
      data-page-fullscreen={pageFullscreen ? '1' : '0'}
    >
      {/* React toolbar stays above native WebView2 (hostRef only below this).
          Hidden during page/video fullscreen so the host fills this rail only. */}
      <form
        data-browser-toolbar
        className="flex gap-1 px-2 py-1.5 border-b shrink-0"
        style={{
          borderColor: 'var(--border)',
          background: 'var(--bg-secondary)',
          position: 'relative',
          zIndex: 2,
          display: pageFullscreen ? 'none' : undefined,
        }}
        onSubmit={(e) => {
          e.preventDefault()
          void go(url)
        }}
      >
        <button
          type="button"
          className="px-1.5 py-1 rounded disabled:opacity-40"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Back"
          aria-label="Back"
          disabled={!loaded || busy}
          onClick={() => {
            if (!isTauri() || !loaded) return
            void tauriInvoke('browser_go_back')
              .then(() => flashStatus('Back', 1200))
              .catch(() => flashStatus('Cannot go back', 2000))
          }}
        >
          ←
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded disabled:opacity-40"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Forward"
          aria-label="Forward"
          disabled={!loaded || busy}
          onClick={() => {
            if (!isTauri() || !loaded) return
            void tauriInvoke('browser_go_forward')
              .then(() => flashStatus('Forward', 1200))
              .catch(() => flashStatus('Cannot go forward', 2000))
          }}
        >
          →
        </button>
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
          style={{
            border: '1px solid var(--border)',
            color: shieldOn ? 'var(--success)' : 'var(--text-muted)',
          }}
          title={
            shieldOn
              ? 'Privacy Shield on — blocks ad/tracker navigations & hides many ads (Settings to toggle)'
              : 'Privacy Shield off — enable in Settings → Project / Browser'
          }
          onClick={() => {
            if (!isTauri()) return
            const next = !shieldOn
            void tauriInvoke<{ enabled?: boolean }>('privacy_shield_set_enabled', {
              enabled: next,
            })
              .then((s) => {
                setShieldOn(Boolean(s?.enabled ?? next))
                flashStatus(
                  s?.enabled ?? next
                    ? 'Privacy Shield on'
                    : 'Privacy Shield off',
                  2200,
                )
              })
              .catch(() => {})
          }}
        >
          {shieldOn ? '🛡' : '○'}
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded disabled:opacity-40"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Reload"
          disabled={!loaded || busy}
          onClick={() => {
            if (!isTauri() || !loaded) return
            void tauriInvoke('browser_reload')
              .then(() => flashStatus('Reloaded', 1800))
              .catch(() => void go(activeUrl))
          }}
        >
          ↻
        </button>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onFocus={(e) => {
            urlEditing.current = true
            // Select all for quick replace (omnibox-style), once per focus
            e.currentTarget.select()
          }}
          onBlur={() => {
            // Defer so a click on Go still sees the typed value first
            window.setTimeout(() => {
              urlEditing.current = false
            }, 180)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              // Restore live URL and leave the bar
              e.preventDefault()
              urlEditing.current = false
              setUrl(activeUrl || home)
              e.currentTarget.blur()
            }
          }}
          className="flex-1 min-w-0 rounded px-1.5 py-1 outline-none font-mono text-[11px]"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          placeholder="Search or enter URL"
          aria-label="Browser address — URL or search"
          spellCheck={false}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
        />
        <button
          type="submit"
          className="px-2 py-1 rounded font-medium disabled:opacity-60"
          style={{ background: 'var(--accent)', color: '#fff' }}
          disabled={busy}
          title={busy ? 'Loading…' : 'Navigate'}
        >
          {busy ? '…' : 'Go'}
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{
            border: '1px solid var(--border)',
            color: isBookmarked(activeUrl || url, bookmarks)
              ? 'var(--accent)'
              : 'var(--text-secondary)',
          }}
          title={
            isBookmarked(activeUrl || url, bookmarks)
              ? 'Remove bookmark'
              : 'Bookmark this page'
          }
          disabled={!normalizeBrowserUrl(activeUrl || url)}
          onClick={() => {
            const target = activeUrl || url
            const { list, added } = toggleBookmark(target)
            setBookmarks(list)
            flashStatus(added ? 'Bookmarked' : 'Bookmark removed', 2000)
          }}
        >
          {isBookmarked(activeUrl || url, bookmarks) ? '★' : '☆'}
        </button>
        <div className="relative" ref={bmPanelRef}>
          <button
            type="button"
            className="px-1.5 py-1 rounded"
            style={{
              border: '1px solid var(--border)',
              color: bmOpen ? 'var(--accent)' : 'var(--text-secondary)',
            }}
            title="Bookmarks"
            aria-expanded={bmOpen}
            onClick={() => setBmOpen((o) => !o)}
          >
            ☰
          </button>
          {bmOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-30 rounded-lg py-1 min-w-[220px] max-w-[320px] max-h-[280px] overflow-auto shadow-xl"
              style={{
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
              }}
              role="listbox"
              aria-label="Bookmarks"
            >
              {bookmarks.length === 0 ? (
                <div
                  className="px-3 py-2 text-[11px]"
                  style={{ color: 'var(--text-muted)' }}
                >
                  No bookmarks yet. Star a page to save it here.
                </div>
              ) : (
                bookmarks.map((b) => (
                  <div
                    key={b.id}
                    className="flex items-center gap-1 px-1"
                    role="option"
                    aria-selected={false}
                  >
                    <button
                      type="button"
                      className="flex-1 min-w-0 text-left px-2 py-1.5 rounded text-[11px] truncate"
                      style={{ color: 'var(--text-primary)' }}
                      title={b.url}
                      onClick={() => {
                        setBmOpen(false)
                        void go(b.url)
                      }}
                    >
                      <span className="font-medium">{b.title}</span>
                      <span
                        className="block truncate"
                        style={{ color: 'var(--text-muted)', fontSize: 10 }}
                      >
                        {b.url}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="px-1.5 py-1 rounded shrink-0"
                      style={{ color: 'var(--text-muted)' }}
                      title="Remove"
                      onClick={() => {
                        setBookmarks(toggleBookmark(b.url).list)
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="px-1.5 py-1 rounded font-medium text-[10px] whitespace-nowrap"
          style={{
            border: `1px solid ${desktopSite ? 'var(--accent)' : 'var(--border)'}`,
            color: desktopSite ? 'var(--accent)' : 'var(--text-secondary)',
            background: desktopSite
              ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
              : 'transparent',
            minWidth: 52,
          }}
          title={
            desktopSite
              ? 'Desktop site on — full layout UA. Click for mobile view (better in this rail).'
              : 'Mobile view (default) — compact layout for the rail. Click for desktop site.'
          }
          aria-pressed={desktopSite}
          aria-label={desktopSite ? 'Desktop site on' : 'Mobile view on'}
          onClick={() => {
            if (!isTauri()) {
              flashStatus('Mobile/Desktop view needs the desktop app (WebView2)', 4000)
              return
            }
            const next = !desktopSite
            const prev = desktopSite
            // Optimistic UI
            setDesktopSite(next)
            setBusy(true)
            setStatus(next ? 'Desktop site — reloading…' : 'Mobile view — reloading…')
            const b = readBounds(hostRef.current)
            const target = activeUrl || url || home
            void tauriInvoke<{
              desktop_site?: boolean
              method?: string
              recreate?: boolean
            }>('browser_set_desktop_site', {
              enabled: next,
              url: target,
              bounds: b ?? undefined,
            })
              .then((s) => {
                const on = Boolean(s?.desktop_site ?? next)
                setDesktopSite(on)
                setLoaded(true)
                const how = s?.method ? ` (${s.method})` : ''
                flashStatus(on ? `Desktop site${how}` : `Mobile view${how}`, 2500)
                // Ensure host bounds + paint after reload
                window.requestAnimationFrame(() => void pushBounds())
                window.setTimeout(() => void pushBounds(), 100)
                window.setTimeout(() => void pushBounds(), 400)
              })
              .catch((e: unknown) => {
                setDesktopSite(prev)
                const msg = e instanceof Error ? e.message : String(e)
                flashStatus(
                  msg.includes('not allowed') || msg.includes('Forbidden')
                    ? 'View mode blocked — rebuild Desktop with browser permissions'
                    : `View mode failed: ${msg}`,
                  6000,
                )
                // Last-resort SPA path: try full navigate with stored flag
                void goRef.current(target).catch(() => {})
              })
              .finally(() => setBusy(false))
          }}
        >
          {desktopSite ? '🖥 Desktop' : '📱 Mobile'}
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

      {/* Host rect ONLY: native WebView2 is positioned over this box — never the popout chrome.
          overflow:hidden clips the *host* (native HWND is separate); keep min-h so bounds stay tall. */}
      <div
        ref={hostRef}
        className="flex-1 min-h-0 relative w-full"
        style={{
          // Match app chrome — white flash/border around WebView was distracting
          background: pageFullscreen ? '#000' : 'var(--bg-primary)',
          minHeight: pageFullscreen ? 0 : 160,
          flex: '1 1 0%',
          zIndex: 1,
          isolation: 'isolate',
          // Let the native child own scrolling; don't clip hit-testing oddly
          overflow: 'visible',
        }}
        data-browser-embed-host
        data-page-fullscreen={pageFullscreen ? '1' : '0'}
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

      {!pageFullscreen && (
        <div
          data-browser-status
          className="px-2 py-1 border-t truncate shrink-0 flex items-center gap-2"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          title={status || activeUrl || undefined}
          role="status"
        >
          <span className="truncate flex-1 min-w-0">
            {status || (loaded ? activeUrl : 'Ready')}
          </span>
          {busy && (
            <span className="shrink-0 text-[10px] tabular-nums" style={{ color: 'var(--accent)' }}>
              …
            </span>
          )}
          {loaded && desktopSite && !status && (
            <span className="shrink-0 text-[10px]" title="Desktop site mode">
              🖥
            </span>
          )}
        </div>
      )}
    </div>
  )
}
