/**
 * Server menu — the connection indicator on the status bar is a button that
 * opens the Remedy server panel: what is running, why it stopped last time,
 * the last lines it printed, and the levers to bring it back.
 *
 * Desktop (Tauri) gets the full panel from `get_server_info`; the web UI and
 * the phone portal get the lighter version (status, version, reconnect).
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { createPortal } from 'react-dom'
import { getServerUrl } from '../api/client'
import { getConnect, type ConnectStatus } from '../api/connect'
import { isTauri, tauriInvoke, tauriListen } from '../api/tauri'
import { browserStackHold } from '../utils/browserStack'

export type ServerIndicatorStatus = 'connected' | 'disconnected' | 'checking'

/** Snapshot returned by the desktop `get_server_info` command. */
export interface ServerInfo {
  mode: 'managed' | 'attached'
  running: boolean
  healthy: boolean
  pid: number | null
  started_at_ms: number | null
  launch_cmd: string | null
  api_origin: string
  port: number
  unexpected_exits: number
  last_exit_status: string | null
  last_exit_at_ms: number | null
  recovery_attempts: number
  recovered_at_ms: number | null
  last_error: string | null
  output: string[]
  logs_dir: string
  data_dir: string
  desktop_version: string
}

/** Payload of the desktop `server-exited` event. */
export interface ServerExitedPayload {
  status: string
  at_ms: number
}

/** Gateway health as GET /api/connect reports it (0.50.1+). */
export interface GatewayHealth {
  crashes: number
  last_crash: string
  last_crash_ts: number
  healing: boolean
  serving: boolean
  thread_alive: boolean
  listening: [string, number] | null
}

/** Must sit above composer (z-index 5) and status-bar chrome. */
const MENU_Z = 550
const REFRESH_MS = 4000

export function formatUptime(startedAtMs: number | null | undefined, now = Date.now()): string {
  if (!startedAtMs || startedAtMs > now) return '—'
  const s = Math.floor((now - startedAtMs) / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

export function formatAgo(atMs: number | null | undefined, now = Date.now()): string {
  if (!atMs) return ''
  const s = Math.max(0, Math.floor((now - atMs) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.floor(h / 24)} d ago`
}

/**
 * Turn an OS exit status string into something a person can act on.
 * Rust's `ExitStatus` prints `exit code: N` on Windows/Linux and
 * `signal: N (SIGKILL)` on Unix; Windows NTSTATUS crashes come back as
 * large unsigned codes.
 */
export function describeExit(status: string | null | undefined): string {
  const raw = String(status || '').trim()
  if (!raw) return ''
  const codeMatch = raw.match(/exit code:\s*(-?\d+)/i) || raw.match(/^(-?\d+)$/)
  if (codeMatch) {
    const n = Number(codeMatch[1])
    const unsigned = n < 0 ? n + 0x1_0000_0000 : n
    const known: Record<number, string> = {
      0: 'exited cleanly (something asked it to stop)',
      1: 'exited with an error (see the last output below)',
      2: 'refused to start: bad arguments or another instance holds the lock',
      3: 'API server failed to start (port busy or startup error)',
      0xc0000005: 'access violation — a native crash inside the server',
      0xc0000409: 'stack buffer overrun / fast-fail — a native crash',
      0xc000013a: 'closed by a console Ctrl+C / Ctrl+Break event',
      0xc0000374: 'heap corruption — a native crash',
      0xe0434352: '.NET/CLR exception in a native component',
      0xc00000fd: 'stack overflow — a native crash',
    }
    const hint = known[unsigned]
    const hex = unsigned > 255 ? ` (0x${unsigned.toString(16).toUpperCase()})` : ''
    return hint ? `${raw}${hex} — ${hint}` : `${raw}${hex}`
  }
  const sig = raw.match(/signal:\s*(\d+)(?:\s*\((\w+)\))?/i)
  if (sig) {
    const name = sig[2] || `signal ${sig[1]}`
    if (name === 'SIGKILL' || sig[1] === '9') return `${raw} — killed by the OS or another process`
    if (name === 'SIGSEGV' || sig[1] === '11') return `${raw} — segmentation fault, a native crash`
    if (name === 'SIGTERM' || sig[1] === '15') return `${raw} — asked to terminate`
    return raw
  }
  return raw
}

export function buildDiagnostics(
  info: ServerInfo | null,
  connect: ConnectStatus | null,
  gateway: GatewayHealth | null,
  status: ServerIndicatorStatus,
  version: string,
): string {
  const lines: string[] = []
  lines.push(`Remedy server diagnostics — ${new Date().toISOString()}`)
  lines.push(`indicator: ${status}  api version: ${version || '?'}`)
  if (info) {
    lines.push(
      `mode: ${info.mode}  running: ${info.running}  healthy: ${info.healthy}  pid: ${info.pid ?? '-'}  uptime: ${formatUptime(info.started_at_ms)}`,
    )
    lines.push(`origin: ${info.api_origin}  desktop: ${info.desktop_version}`)
    lines.push(`launch: ${info.launch_cmd || '-'}`)
    lines.push(`unexpected exits: ${info.unexpected_exits}`)
    if (info.last_exit_status) {
      lines.push(
        `last exit: ${describeExit(info.last_exit_status)} ${formatAgo(info.last_exit_at_ms)} — recovery attempts ${info.recovery_attempts}, recovered: ${info.recovered_at_ms ? formatAgo(info.recovered_at_ms) : 'no'}`,
      )
    }
    if (info.last_error) lines.push(`last error: ${info.last_error}`)
    lines.push(`logs: ${info.logs_dir}`)
  }
  if (connect) {
    lines.push(
      `connect: enabled=${connect.enabled} paused=${connect.paused} listening=${connect.listening ? connect.listening.join(':') : 'none'} devices=${connect.devices.length}`,
    )
  }
  if (gateway) {
    lines.push(
      `gateway: serving=${gateway.serving} alive=${gateway.thread_alive} crashes=${gateway.crashes} healing=${gateway.healing}${gateway.last_crash ? ` last=${gateway.last_crash}` : ''}`,
    )
  }
  if (info && info.output.length) {
    lines.push('', '--- last server output ---', ...info.output.slice(-60))
  }
  return lines.join('\n')
}

function parseGateway(raw: unknown): GatewayHealth | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const listening = Array.isArray(o.listening) && o.listening.length === 2
    ? ([String(o.listening[0]), Number(o.listening[1])] as [string, number])
    : null
  return {
    crashes: Number(o.crashes || 0),
    last_crash: String(o.last_crash || ''),
    last_crash_ts: Number(o.last_crash_ts || 0),
    healing: Boolean(o.healing),
    serving: Boolean(o.serving),
    thread_alive: Boolean(o.thread_alive),
    listening,
  }
}

interface ServerMenuProps {
  status: ServerIndicatorStatus
  version: string
  /** Phone portal / narrow strip: label carries alerts and the menu is lighter. */
  compact?: boolean
  /** Extra text after "Connected" (pending approvals, active goal). */
  alerts?: string
  /** Called after a restart request resolves so the bar can re-check. */
  onRestarted?: () => void
}

type MenuPos = { left: number; bottom: number; maxH: number; width: number }

export function ServerMenu({
  status,
  version,
  compact = false,
  alerts = '',
  onRestarted,
}: ServerMenuProps) {
  const tauri = isTauri()
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<MenuPos | null>(null)
  const [info, setInfo] = useState<ServerInfo | null>(null)
  const [connect, setConnect] = useState<ConnectStatus | null>(null)
  const [gateway, setGateway] = useState<GatewayHealth | null>(null)
  const [busy, setBusy] = useState<'' | 'restart' | 'copy'>('')
  const [note, setNote] = useState('')
  const [showOutput, setShowOutput] = useState(false)
  const [lastExit, setLastExit] = useState<ServerExitedPayload | null>(null)
  const [now, setNow] = useState(Date.now())
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    if (tauri) {
      try {
        const next = await tauriInvoke<ServerInfo>('get_server_info')
        setInfo(next)
      } catch {
        /* older desktop without the command */
      }
    }
    if (status === 'connected') {
      try {
        const raw = await getConnect()
        setConnect(raw)
        setGateway(parseGateway((raw as unknown as Record<string, unknown>).gateway))
      } catch {
        setConnect(null)
        setGateway(null)
      }
    } else {
      setConnect(null)
      setGateway(null)
    }
    setNow(Date.now())
  }, [tauri, status])

  // The exit badge: the desktop tells us the server died even while the
  // panel is closed, so the indicator can say so before anyone asks.
  useEffect(() => {
    if (!tauri) return
    let off: (() => void) | null = null
    let cancelled = false
    void tauriListen<ServerExitedPayload>('server-exited', (p) => {
      if (cancelled) return
      setLastExit(p && typeof p === 'object' ? p : null)
    }).then((fn) => {
      if (cancelled) fn()
      else off = fn
    })
    return () => {
      cancelled = true
      off?.()
    }
  }, [tauri])

  useEffect(() => {
    if (!open) return
    void refresh()
    const iv = setInterval(() => void refresh(), REFRESH_MS)
    return () => clearInterval(iv)
  }, [open, refresh])

  const place = () => {
    const btn = btnRef.current
    if (!btn) return
    const r = btn.getBoundingClientRect()
    const width = Math.min(Math.max(300, Math.floor(window.innerWidth * 0.9)), 460)
    const spaceAbove = r.top - 8
    const spaceBelow = window.innerHeight - r.bottom - 8
    const openUp = spaceAbove >= 240 || spaceAbove >= spaceBelow
    const maxH = Math.min(560, Math.max(220, openUp ? spaceAbove : spaceBelow))
    let left = r.left
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8))
    if (openUp) {
      setPos({ left, bottom: window.innerHeight - r.top + 6, maxH, width })
    } else {
      setPos({ left, bottom: Math.max(8, window.innerHeight - r.bottom - maxH - 6), maxH, width })
    }
  }

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    place()
    const onWin = () => place()
    window.addEventListener('resize', onWin)
    window.addEventListener('scroll', onWin, true)
    return () => {
      window.removeEventListener('resize', onWin)
      window.removeEventListener('scroll', onWin, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        btnRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // The panel paints under the native Browser HWND unless we suppress it.
  useEffect(() => {
    if (!open) return
    return browserStackHold('server-menu')
  }, [open])

  const restart = async () => {
    if (!tauri || busy) return
    setBusy('restart')
    setNote('Restarting the Remedy server…')
    try {
      await tauriInvoke('restart_server')
      setNote('Server restarted.')
      setLastExit(null)
      onRestarted?.()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
      void refresh()
    }
  }

  const copyDiagnostics = async () => {
    setBusy('copy')
    try {
      const text = buildDiagnostics(info, connect, gateway, status, version)
      await navigator.clipboard.writeText(text)
      setNote('Diagnostics copied.')
    } catch {
      setNote('Could not copy — clipboard unavailable.')
    } finally {
      setBusy('')
    }
  }

  const openFolder = async (cmd: 'open_logs_folder' | 'open_data_folder') => {
    try {
      await tauriInvoke(cmd)
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    }
  }

  const dotColor =
    status === 'connected' ? 'var(--success)' : status === 'checking' ? 'var(--warning)' : 'var(--error)'
  const label =
    status === 'connected'
      ? compact && alerts
        ? `Connected · ${alerts}`
        : 'Connected'
      : status === 'checking'
        ? 'Connecting…'
        : 'Offline'
  const exitBadge = lastExit && status !== 'disconnected' ? 'recovered' : ''
  const title =
    status === 'connected'
      ? `Remedy ${version || ''} — server options`.trim()
      : 'Server offline — open server options'

  const uptime = formatUptime(info?.started_at_ms, now)
  const lastExitText = info?.last_exit_status
    ? describeExit(info.last_exit_status)
    : lastExit
      ? describeExit(lastExit.status)
      : ''
  const lastExitAgo = formatAgo(info?.last_exit_at_ms ?? lastExit?.at_ms ?? null, now)
  const phones = connect?.devices.length ?? 0

  const panelStyle: CSSProperties | undefined = pos
    ? {
        position: 'fixed',
        zIndex: MENU_Z,
        left: pos.left,
        bottom: pos.bottom,
        width: pos.width,
        maxHeight: pos.maxH,
        overflowY: 'auto',
        background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
        border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
        boxShadow:
          '0 12px 32px rgba(0,0,0,0.35), 0 0 0 1px color-mix(in srgb, var(--accent) 6%, transparent)',
        backdropFilter: 'blur(12px)',
        color: 'var(--text-primary)',
      }
    : undefined

  const row = (k: string, v: string, tone?: 'muted' | 'warn' | 'error') => (
    <div className="flex items-baseline gap-2 text-[11px] leading-snug" key={k}>
      <span className="w-[5.5rem] flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
        {k}
      </span>
      <span
        className="min-w-0 break-words"
        style={{
          color:
            tone === 'error'
              ? 'var(--error)'
              : tone === 'warn'
                ? 'var(--warning)'
                : tone === 'muted'
                  ? 'var(--text-muted)'
                  : 'var(--text-primary)',
        }}
      >
        {v}
      </span>
    </div>
  )

  const actionBtn = (
    text: string,
    onClick: () => void,
    opts: { primary?: boolean; disabled?: boolean } = {},
  ) => (
    <button
      type="button"
      onClick={onClick}
      disabled={opts.disabled}
      className="px-2 py-1 rounded-md text-[11px] font-medium"
      style={{
        background: opts.primary ? 'var(--accent)' : 'transparent',
        color: opts.primary ? '#fff' : 'var(--text-secondary)',
        border: opts.primary ? '1px solid transparent' : '1px solid var(--border)',
        opacity: opts.disabled ? 0.6 : 1,
        cursor: opts.disabled ? 'default' : 'pointer',
      }}
    >
      {text}
    </button>
  )

  const panel =
    open && pos
      ? createPortal(
          <div
            ref={panelRef}
            role="dialog"
            aria-label="Remedy server"
            data-remedy-server-menu
            className="rounded-xl p-3 flex flex-col gap-2.5 outline-none"
            style={panelStyle}
          >
            <div className="flex items-center gap-2">
              <span
                className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: dotColor }}
                aria-hidden
              />
              <span className="text-xs font-semibold">
                {status === 'connected'
                  ? 'Remedy server is running'
                  : status === 'checking'
                    ? 'Reaching the Remedy server…'
                    : 'Remedy server is offline'}
              </span>
              <span className="flex-1" />
              {version && (
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  v{version}
                </span>
              )}
            </div>

            {lastExitText && (
              <div
                className="rounded-lg px-2.5 py-2 text-[11px] leading-snug"
                style={{
                  background: 'color-mix(in srgb, var(--error) 10%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--error) 35%, transparent)',
                }}
              >
                <div className="font-medium" style={{ color: 'var(--error)' }}>
                  Server stopped unexpectedly{lastExitAgo ? ` ${lastExitAgo}` : ''}
                </div>
                <div style={{ color: 'var(--text-secondary)' }}>{lastExitText}</div>
                {info && (
                  <div style={{ color: 'var(--text-muted)' }}>
                    {info.recovered_at_ms
                      ? `Recovered automatically after ${info.recovery_attempts || 1} attempt${(info.recovery_attempts || 1) === 1 ? '' : 's'}.`
                      : info.recovery_attempts > 0
                        ? `Recovery tried ${info.recovery_attempts}× and failed — restart below.`
                        : ''}
                    {info.unexpected_exits > 1 ? ` ${info.unexpected_exits} exits this session.` : ''}
                  </div>
                )}
              </div>
            )}

            {info?.last_error && !lastExitText && row('Error', info.last_error, 'error')}

            <div className="flex flex-col gap-1">
              {row('API', info?.api_origin || getServerUrl())}
              {tauri && info && row('Process', info.mode === 'attached'
                ? 'attached to a server you started (not managed)'
                : info.running
                  ? `managed · pid ${info.pid ?? '?'} · up ${uptime}`
                  : 'managed · not running', info.running || info.mode === 'attached' ? undefined : 'error')}
              {tauri && info && row('Health', info.healthy ? 'answering /api/status' : 'not answering', info.healthy ? undefined : 'warn')}
              {status === 'connected' && connect && row(
                'Phones',
                !connect.enabled
                  ? 'Connect is off'
                  : connect.paused
                    ? 'paused'
                    : `${phones} paired · ${connect.listening ? `listening on ${connect.listening[0]}:${connect.listening[1]}` : 'gateway not listening'}`,
                connect.enabled && !connect.paused && !connect.listening ? 'warn' : undefined,
              )}
              {gateway && (gateway.crashes > 0 || gateway.healing) && row(
                'Gateway',
                gateway.healing
                  ? 'phone gateway restarting…'
                  : `phone gateway recovered ${gateway.crashes}× — ${gateway.last_crash}`,
                'warn',
              )}
            </div>

            <div className="flex flex-wrap gap-1.5">
              {tauri
                ? actionBtn(busy === 'restart' ? 'Restarting…' : 'Restart server', () => void restart(), {
                    primary: status !== 'connected',
                    disabled: busy === 'restart',
                  })
                : actionBtn('Reconnect', () => window.location.reload(), { primary: status !== 'connected' })}
              {tauri && actionBtn('Open logs', () => void openFolder('open_logs_folder'))}
              {tauri && actionBtn('Data folder', () => void openFolder('open_data_folder'))}
              {actionBtn(busy === 'copy' ? 'Copying…' : 'Copy diagnostics', () => void copyDiagnostics(), {
                disabled: busy === 'copy',
              })}
            </div>

            {note && (
              <div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>
                {note}
              </div>
            )}

            {tauri && info && info.output.length > 0 && (
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  className="text-left text-[11px] font-medium"
                  style={{ color: 'var(--text-secondary)' }}
                  onClick={() => setShowOutput((v) => !v)}
                  aria-expanded={showOutput}
                >
                  {showOutput ? '▾' : '▸'} Last server output ({info.output.length} lines)
                </button>
                {showOutput && (
                  <pre
                    className="rounded-md p-2 text-[10px] leading-snug overflow-auto whitespace-pre-wrap break-words"
                    style={{
                      maxHeight: 220,
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border)',
                      color: 'var(--text-secondary)',
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                    }}
                  >
                    {info.output.slice(-80).join('\n')}
                  </pre>
                )}
              </div>
            )}
            {tauri && info && (
              <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }} title={info.logs_dir}>
                Logs: {info.logs_dir}
              </div>
            )}
          </div>,
          document.body,
        )
      : null

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        data-remedy-server-indicator
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={title}
        className="flex items-center gap-1.5 flex-shrink-0 min-w-0 rounded px-1 -mx-1"
        style={{ background: open ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent' }}
      >
        <span
          className={`inline-block w-2 h-2 rounded-full${status === 'disconnected' ? ' status-offline-dot' : ''}`}
          style={{ background: dotColor }}
          aria-hidden
        />
        <span className="font-medium truncate" style={{ color: 'var(--text-secondary)' }}>
          {label}
        </span>
        {exitBadge && (
          <span
            className="px-1 rounded text-[10px] font-medium flex-shrink-0"
            style={{
              color: 'var(--warning)',
              background: 'color-mix(in srgb, var(--warning) 14%, transparent)',
            }}
            title="The server stopped unexpectedly and was restarted — open for details"
          >
            {exitBadge}
          </span>
        )}
        <span aria-hidden className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
          ▾
        </span>
      </button>
      {panel}
    </>
  )
}
