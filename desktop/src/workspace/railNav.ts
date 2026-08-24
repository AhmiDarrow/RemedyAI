/** Pending rail context for app_control (path / url / cwd). */

export const FILES_SET_PATH_EVENT = 'remedy:files-set-path'
export const TERMINAL_SET_CWD_EVENT = 'remedy:terminal-set-cwd'
export const BROWSER_SET_URL_EVENT = 'remedy:browser-set-url'
export const SCRATCH_RELOAD_EVENT = 'remedy:scratch-reload'

let pendingFiles: string | null = null
let pendingCwd: string | null = null
let pendingUrl: string | null = null

function dispatch(name: string, detail: Record<string, string>) {
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent(name, { detail }))
  }
}

export function requestFilesPath(path: string): void {
  const p = path.trim()
  if (!p) return
  pendingFiles = p
  dispatch(FILES_SET_PATH_EVENT, { path: p })
}

export function takePendingFilesPath(): string | null {
  const p = pendingFiles
  pendingFiles = null
  return p
}

export function requestTerminalCwd(path: string): void {
  const p = path.trim()
  if (!p) return
  pendingCwd = p
  dispatch(TERMINAL_SET_CWD_EVENT, { path: p })
}

export function takePendingTerminalCwd(): string | null {
  const p = pendingCwd
  pendingCwd = null
  return p
}

export function requestBrowserUrl(url: string): void {
  const u = url.trim()
  if (!u) return
  pendingUrl = u
  dispatch(BROWSER_SET_URL_EVENT, { url: u })
}

export function takePendingBrowserUrl(): string | null {
  const p = pendingUrl
  pendingUrl = null
  return p
}

export function requestScratchReload(): void {
  dispatch(SCRATCH_RELOAD_EVENT, {})
}
