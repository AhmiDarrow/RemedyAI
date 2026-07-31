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

type Bounds = { x: number; y: number; width: number; height: number }

/** Keep native embed below app title / popout chrome and above status bar. */
function chromeSafeBand(hostRect: DOMRect): { minY: number; maxBottom: number } {
  let minY = 0
  let maxBottom = window.innerHeight

  const title = document.querySelector('.titlebar')
  if (title) {
    minY = Math.max(minY, title.getBoundingClientRect().bottom)
  } else {
    // Custom title strip is 36px when present
    minY = Math.max(minY, 36)
  }

  const popChrome = document.querySelector('[data-popout-chrome]')
  if (popChrome) {
    const pr = popChrome.getBoundingClientRect()
    if (pr.height > 8 && pr.bottom > minY) {
      minY = Math.max(minY, pr.bottom)
    }
  }

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

  const status = document.querySelector('[data-remedy-status-bar]')
  if (status) {
    maxBottom = Math.min(maxBottom, status.getBoundingClientRect().top)
  }

  // Browser slide status strip under the host
  for (const st of document.querySelectorAll('[data-browser-status]')) {
    const sr = st.getBoundingClientRect()
    if (sr.height < 4) continue
    if (sr.right < hostRect.left + 4 || sr.left > hostRect.right - 4) continue
    if (sr.top >= hostRect.top) {
      maxBottom = Math.min(maxBottom, sr.top)
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

  const { minY, maxBottom } = chromeSafeBand(r)
  const x = Math.round(r.left)
  const y = Math.round(Math.max(r.top, minY))
  const right = Math.round(r.right)
  const bottom = Math.round(Math.min(r.bottom, maxBottom))
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
  const [bookmarks, setBookmarks] = useState<BrowserBookmark[]>(() => loadBookmarks())
  const [bmOpen, setBmOpen] = useState(false)
  const bmPanelRef = useRef<HTMLDivElement | null>(null)
  const [shieldOn, setShieldOn] = useState(true)
  /** false = mobile UA (default, better in narrow rail); true = desktop site */
  const [desktopSite, setDesktopSite] = useState(false)

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
        setStatus('Enter a URL or search')
        return
      }
      urlEditing.current = false
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

  const applyLiveUrl = useCallback((raw: string) => {
    const u = (raw || '').trim()
    if (!u || u.startsWith('about:')) return
    setActiveUrl(u)
    // Only rewrite the address bar when the user is not typing a new URL
    if (!urlEditing.current) {
      setUrl(u)
    }
  }, [])

  // Sync address bar when agent/Rust navigates (does not reload the page).
  useEffect(() => {
    const onSetUrl = (ev: Event) => {
      const u = (ev as CustomEvent<{ url?: string }>).detail?.url
      if (!u) return
      applyLiveUrl(u)
      setLoaded(true)
      setStatus(`Loaded ${u}`)
      autoStarted.current = true
      void pushBounds()
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
              setStatus(`Restored ${cur}`)
            } else {
              setStatus('Browser ready')
            }
          } catch {
            setStatus('Browser ready')
          }
          await pushBounds()
          await tauriInvoke('browser_show').catch(() => {})
          return
        }
      } catch {
        /* */
      }
      const b = readBounds(hostRef.current)
      if (b) {
        autoStarted.current = true
        void goRef.current(home)
        return
      }
      if (attempts < 40) {
        window.setTimeout(() => void tick(), 50)
      } else {
        setStatus('Expand Browser rail, then press Go (or ↗ for system browser)')
      }
    }
    const id = window.requestAnimationFrame(() => void tick())
    return () => {
      cancelled = true
      window.cancelAnimationFrame(id)
    }
  }, [home, pushBounds])

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
        data-browser-toolbar
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
                setStatus(
                  s?.enabled ?? next
                    ? 'Privacy Shield on'
                    : 'Privacy Shield off',
                )
              })
              .catch(() => {})
          }}
        >
          {shieldOn ? '🛡' : '○'}
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
          onFocus={() => {
            urlEditing.current = true
          }}
          onBlur={() => {
            // Defer so a click on Go still sees the typed value first
            window.setTimeout(() => {
              urlEditing.current = false
            }, 180)
          }}
          className="flex-1 min-w-0 rounded px-1.5 py-1 outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          placeholder="Search or enter URL"
          aria-label="Browser address — URL or search"
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
            setStatus(added ? 'Bookmarked' : 'Bookmark removed')
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
          className="px-1.5 py-1 rounded font-medium"
          style={{
            border: '1px solid var(--border)',
            color: desktopSite ? 'var(--accent)' : 'var(--text-secondary)',
            background: desktopSite ? 'var(--bg-primary)' : 'transparent',
            minWidth: 28,
          }}
          title={
            desktopSite
              ? 'Desktop site on — click for mobile view (better in this rail)'
              : 'Mobile view (default) — click to request desktop site'
          }
          onClick={() => {
            if (!isTauri()) return
            const next = !desktopSite
            void tauriInvoke<{ desktop_site?: boolean }>('browser_set_desktop_site', {
              enabled: next,
            })
              .then((s) => {
                const on = Boolean(s?.desktop_site ?? next)
                setDesktopSite(on)
                setLoaded(false)
                setStatus(on ? 'Desktop site — reloading…' : 'Mobile view — reloading…')
                // Recreate embed with new UA + reload current URL
                const target = activeUrl || url || home
                window.setTimeout(() => {
                  void goRef.current(target)
                }, 80)
              })
              .catch((e: unknown) => {
                setStatus(e instanceof Error ? e.message : String(e))
              })
          }}
        >
          {desktopSite ? '🖥' : '📱'}
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
          background: 'var(--bg-primary)',
          minHeight: 160,
          zIndex: 1,
          isolation: 'isolate',
          // Let the native child own scrolling; don't clip hit-testing oddly
          overflow: 'visible',
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
          data-browser-status
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
