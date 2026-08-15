import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { isLinuxDesktop } from '../../utils/platform'

const SHELL_LABEL = isLinuxDesktop() ? 'Terminal' : 'PowerShell'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/** Read app theme tokens so xterm matches light/dark forest/etc. */
function readTerminalTheme(): {
  background: string
  foreground: string
  cursor: string
  cursorAccent: string
  selectionBackground: string
} {
  const cs = getComputedStyle(document.documentElement)
  const bg = cs.getPropertyValue('--bg-primary').trim() || '#0b0f14'
  const fg = cs.getPropertyValue('--text-primary').trim() || '#e6edf3'
  const accent = cs.getPropertyValue('--accent').trim() || '#c4b5fd'
  const tertiary = cs.getPropertyValue('--bg-tertiary').trim() || '#3b3266'
  return {
    background: bg,
    foreground: fg,
    cursor: accent,
    cursorAccent: bg,
    selectionBackground: tertiary,
  }
}

/**
 * In-app PowerShell via ConPTY + xterm.
 * Waits until the host has a real size before spawning (avoids 0×0 PTY fails).
 * Restarts the shell when the session project path changes.
 */
export function TerminalSlide({ sessionId }: { sessionId?: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const ptyIdRef = useRef<string | null>(null)
  const cwdRef = useRef('')
  const startedRef = useRef(false)
  /** Bumped on each start/restart so a slow open cannot clobber a newer shell. */
  const ptyGenRef = useRef(0)
  const [status, setStatus] = useState(`Starting ${SHELL_LABEL}…`)
  const [cwd, setCwd] = useState('')
  const [hostBg, setHostBg] = useState(() => {
    try {
      return readTerminalTheme().background
    } catch {
      return '#0b0f14'
    }
  })

  // Resolve session project path for PTY cwd
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
        const data = await apiFetch<{ project_path?: string }>(`/workspace${q}`)
        if (cancelled) return
        const next = (data.project_path || '').trim()
        setCwd(next)
        cwdRef.current = next
      } catch {
        if (!cancelled) {
          setCwd('')
          cwdRef.current = ''
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const startPty = useCallback(async (term: Terminal, fit: FitAddon, workdir: string) => {
    if (!isTauri()) {
      term.writeln(`\r\nDesktop app required for in-app ${SHELL_LABEL}.\r\n`)
      setStatus('Not available outside desktop')
      return
    }
    const gen = ++ptyGenRef.current
    try {
      // Close previous session if any
      const prev = ptyIdRef.current
      if (prev) {
        await tauriInvoke('pty_close', { id: prev }).catch(() => {})
        if (gen === ptyGenRef.current) ptyIdRef.current = null
      }
      if (gen !== ptyGenRef.current) return
      fit.fit()
      let cols = term.cols
      let rows = term.rows
      if (cols < 20 || rows < 5) {
        // Host still measuring — retry shortly
        setStatus('Waiting for panel size…')
        await new Promise((r) => window.setTimeout(r, 120))
        if (gen !== ptyGenRef.current) return
        fit.fit()
        cols = Math.max(term.cols, 80)
        rows = Math.max(term.rows, 24)
      }
      setStatus(`Launching ${SHELL_LABEL}…`)
      const id = await tauriInvoke<string>('pty_open', {
        cwd: workdir.trim() || null,
        cols,
        rows,
      })
      if (gen !== ptyGenRef.current) {
        // A newer start won — discard this shell immediately
        await tauriInvoke('pty_close', { id }).catch(() => {})
        return
      }
      ptyIdRef.current = id
      setStatus(workdir ? `${SHELL_LABEL} · ${workdir}` : SHELL_LABEL)
      term.focus()
    } catch (e: unknown) {
      if (gen !== ptyGenRef.current) return
      const msg = e instanceof Error ? e.message : String(e)
      term.writeln(`\r\nFailed to start ${SHELL_LABEL}: ${msg}\r\n`)
      setStatus(`Error: ${msg}`)
    }
  }, [])

  // Mount xterm once
  useEffect(() => {
    const el = hostRef.current
    if (!el) return

    const theme = readTerminalTheme()
    setHostBg(theme.background)

    const isLinux = /Linux|X11|Wayland/i.test(navigator.userAgent || '')
    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: isLinux ? 'bar' : 'block',
      cursorWidth: isLinux ? 1 : 2,
      fontSize: 13,
      fontFamily: isLinux
        ? '"DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", monospace'
        : 'Consolas, "Cascadia Mono", "Courier New", monospace',
      theme,
      allowProposedApi: true,
      rightClickSelectsWord: false,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    // Defer first fit until layout has painted
    requestAnimationFrame(() => {
      try {
        fit.fit()
      } catch {
        /* */
      }
    })
    term.focus()
    termRef.current = term
    fitRef.current = fit

    const focusTerm = () => {
      try {
        term.focus()
      } catch {
        /* */
      }
    }
    el.addEventListener('mousedown', focusTerm)
    el.addEventListener('click', focusTerm)

    const writePty = (data: string) => {
      const id = ptyIdRef.current
      if (!id || !isTauri()) return
      void tauriInvoke('pty_write', { id, data }).catch(() => {})
    }

    const copySelection = async () => {
      const sel = term.getSelection()
      if (!sel) return false
      try {
        await navigator.clipboard.writeText(sel)
        return true
      } catch {
        return false
      }
    }

    const pasteClipboard = async () => {
      try {
        const text = await navigator.clipboard.readText()
        if (text) writePty(text)
      } catch {
        /* clipboard permission denied */
      }
    }

    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true
      // Never swallow Escape — PopoutOverlay uses it to exit fullscreen / close
      if (ev.key === 'Escape') return true
      const key = ev.key.toLowerCase()
      const ctrl = ev.ctrlKey || ev.metaKey
      const shift = ev.shiftKey
      if (ctrl && shift && key === 'c') {
        void copySelection()
        return false
      }
      if (ctrl && shift && key === 'v') {
        void pasteClipboard()
        return false
      }
      if (ctrl && !shift && key === 'c') {
        if (term.hasSelection()) {
          void copySelection()
          return false
        }
        return true
      }
      if (ctrl && !shift && key === 'v') {
        void pasteClipboard()
        return false
      }
      if (ctrl && !shift && key === 'a') {
        term.selectAll()
        return false
      }
      return true
    })

    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault()
      if (term.hasSelection()) {
        void copySelection()
      } else {
        void pasteClipboard()
      }
    }
    el.addEventListener('contextmenu', onContextMenu)

    const onData = term.onData((data) => {
      writePty(data)
    })

    let unData: UnlistenFn | undefined
    let unExit: UnlistenFn | undefined
    let cancelled = false
    startedRef.current = false

    const tryStart = async () => {
      if (cancelled || startedRef.current || !isTauri()) return
      // Need a non-trivial host size
      if (el.clientWidth < 40 || el.clientHeight < 40) return
      startedRef.current = true
      try {
        unData = await listen<{ id: string; data: string }>('pty-data', (ev) => {
          if (ev.payload.id === ptyIdRef.current) {
            term.write(ev.payload.data)
          }
        })
        unExit = await listen<{ id: string }>('pty-exit', (ev) => {
          if (ev.payload.id === ptyIdRef.current) {
            term.writeln('\r\n[shell exited]\r\n')
            ptyIdRef.current = null
            setStatus('Shell exited — Restart to open again')
          }
        })
      } catch (e) {
        term.writeln(`\r\nEvent listen failed: ${e}\r\n`)
      }
      if (!cancelled) {
        await startPty(term, fit, cwdRef.current)
      }
    }

    void (async () => {
      if (!isTauri()) {
        term.writeln(`Desktop app required for in-app ${SHELL_LABEL}.`)
        setStatus('Web UI — use desktop for terminal')
        return
      }
      // Retry until layout has size (panel open animation / rail expand)
      for (let i = 0; i < 20 && !cancelled && !startedRef.current; i++) {
        await tryStart()
        if (!startedRef.current) await new Promise((r) => window.setTimeout(r, 100))
      }
      if (!startedRef.current && !cancelled) {
        // Force start with defaults
        startedRef.current = true
        await startPty(term, fit, cwdRef.current)
      }
    })()

    const ro = new ResizeObserver(() => {
      try {
        fit.fit()
        const id = ptyIdRef.current
        if (id && isTauri()) {
          void tauriInvoke('pty_resize', {
            id,
            cols: term.cols,
            rows: term.rows,
          }).catch(() => {})
        } else if (!startedRef.current && !cancelled) {
          void tryStart()
        }
      } catch {
        /* */
      }
    })
    ro.observe(el)

    // Keep xterm colors in sync when the app theme changes
    const themeObs = new MutationObserver(() => {
      try {
        const next = readTerminalTheme()
        term.options.theme = next
        setHostBg(next.background)
      } catch {
        /* */
      }
    })
    themeObs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    })

    return () => {
      cancelled = true
      themeObs.disconnect()
      onData.dispose()
      ro.disconnect()
      el.removeEventListener('mousedown', focusTerm)
      el.removeEventListener('click', focusTerm)
      el.removeEventListener('contextmenu', onContextMenu)
      void unData?.()
      void unExit?.()
      const id = ptyIdRef.current
      if (id && isTauri()) {
        void tauriInvoke('pty_close', { id }).catch(() => {})
      }
      ptyIdRef.current = null
      startedRef.current = false
      ptyGenRef.current += 1 // invalidate any in-flight pty_open
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [startPty])

  // When session project path changes after shell is up, restart in the new cwd
  const lastCwdForPty = useRef<string | null>(null)
  useEffect(() => {
    const term = termRef.current
    const fit = fitRef.current
    if (!term || !fit || !isTauri()) return
    // Skip first empty→value race on mount (tryStart already uses cwdRef)
    if (!startedRef.current) {
      lastCwdForPty.current = cwd
      return
    }
    if (lastCwdForPty.current === cwd) return
    lastCwdForPty.current = cwd
    let cancelled = false
    void (async () => {
      term.writeln(
        cwd
          ? `\r\n[session project → ${cwd}]\r\nRestarting shell…\r\n`
          : '\r\n[session project cleared]\r\nRestarting shell…\r\n',
      )
      if (!cancelled) await startPty(term, fit, cwd)
    })()
    return () => {
      cancelled = true
    }
  }, [cwd, startPty])

  const restart = async () => {
    const term = termRef.current
    const fit = fitRef.current
    if (!term || !fit) return
    term.clear()
    term.writeln(`Restarting ${SHELL_LABEL}…\r\n`)
    await startPty(term, fit, cwd || cwdRef.current)
  }

  const clearScreen = () => {
    const term = termRef.current
    if (!term) return
    term.clear()
    term.focus()
  }

  const copyCwd = async () => {
    const p = cwd || cwdRef.current
    if (!p) return
    try {
      await navigator.clipboard.writeText(p)
      setStatus(`Copied · ${p}`)
      window.setTimeout(() => {
        setStatus(p ? `${SHELL_LABEL} · ${p}` : SHELL_LABEL)
      }, 1800)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 max-h-full overflow-hidden text-xs">
      <div
        className="px-2 py-1 border-b flex gap-1 items-center shrink-0"
        style={{
          borderColor: 'var(--border)',
          color: 'var(--text-muted)',
          background: 'var(--bg-secondary)',
        }}
      >
        <button
          type="button"
          className="truncate flex-1 min-w-0 text-left font-mono rounded px-0.5"
          style={{ color: 'var(--text-muted)', background: 'transparent', border: 'none' }}
          title={cwd ? `${cwd} — click to copy cwd` : status}
          onClick={() => void copyCwd()}
        >
          {status}
        </button>
        <button
          type="button"
          className="workspace-chrome-btn shrink-0"
          onClick={clearScreen}
          title="Clear terminal display"
          aria-label="Clear terminal"
        >
          ⌫
        </button>
        <button
          type="button"
          className="px-2 py-0.5 rounded font-sans shrink-0"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() => void restart()}
          title={`Restart ${SHELL_LABEL} in session project folder`}
        >
          Restart
        </button>
      </div>
      <div
        ref={hostRef}
        className="flex-1 min-h-0 w-full max-h-full cursor-text overflow-hidden"
        style={{ background: hostBg, position: 'relative' }}
        title="Click to focus · Ctrl+Shift+C/V copy/paste · right-click paste · Esc exits fullscreen"
        onMouseDown={() => termRef.current?.focus()}
      />
    </div>
  )
}
