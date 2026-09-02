/** RemedyConnect — phone remote (Tailscale + LAN). */

import { apiFetch } from './client'
import { relativeTime } from '../utils/relativeTime'

export const CONNECT_PANE_KEYS = [
  'live_ui',
  'chat',
  'approvals',
  'sessions',
  'rails',
  'computer_preview',
  'settings_write',
] as const

export type ConnectPaneKey = (typeof CONNECT_PANE_KEYS)[number]

export type ConnectPanes = Record<ConnectPaneKey, boolean>

export const CONNECT_PANE_LABELS: Record<ConnectPaneKey, string> = {
  live_ui: 'Live host UI',
  chat: 'Chat/stream',
  approvals: 'Approvals/Stop',
  sessions: 'Sessions',
  rails: 'Files/Terminal/Browser rails',
  computer_preview: 'Computer preview',
  settings_write: 'Settings write',
}

export const DEFAULT_CONNECT_PANES: ConnectPanes = {
  live_ui: true,
  chat: true,
  approvals: true,
  sessions: true,
  rails: true,
  computer_preview: false,
  settings_write: false,
}

export interface ConnectDevice {
  id: string
  name: string
  paired_at?: string
}

export interface ConnectStatus {
  enabled: boolean
  paused: boolean
  bind_host: string
  bind_port: number
  panes: ConnectPanes
  relay_url: string
  devices: ConnectDevice[]
  addresses?: string[]
  qr?: string | null
  /**
   * What the Connect listener is actually bound to, per the server:
   * `[host, port]` when up, `null` when enabled but nothing is bound
   * (bind failed, or not started yet), `undefined` when the payload did
   * not say (PUT echo, offline fallback).
   */
  listening?: [string, number] | null
}

export type ConnectListening = [string, number]

export interface ConnectPutBody {
  enabled: boolean
  bind_host: string
  bind_port: number
  paused: boolean
  panes: ConnectPanes
  relay_url: string
}

export interface ConnectPairStart {
  qr: string
  exp?: number
}

const IPV4 = /^(?:\d{1,3}\.){3}\d{1,3}$/

function asRecord(v: unknown): Record<string, unknown> | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  return v as Record<string, unknown>
}

function isLoopbackIPv4(host: string): boolean {
  return host === '127.0.0.1' || host.startsWith('127.')
}

/** Drop 0.0.0.0, wildcards, and non-IPv4. LAN first; loopback last. */
export function filterConnectAddresses(addrs: unknown): string[] {
  if (!Array.isArray(addrs)) return []
  const lan: string[] = []
  const loop: string[] = []
  const seen = new Set<string>()
  for (const item of addrs) {
    if (typeof item !== 'string') continue
    const host = ipv4Candidate(item)
    if (!host || seen.has(host)) continue
    seen.add(host)
    if (isLoopbackIPv4(host)) loop.push(host)
    else lan.push(host)
  }
  return [...lan, ...loop]
}

export function isConnectLoopbackBind(host: string): boolean {
  return isLoopbackIPv4((host || '').trim())
}

function ipv4Candidate(raw: string): string | null {
  const s = raw.trim()
  if (!s || s === '*' || s.includes('*')) return null
  const noCidr = s.split('/')[0] || ''
  const host = noCidr.includes(':') ? noCidr.split(':')[0] || '' : noCidr
  if (!host || host === '0.0.0.0' || !IPV4.test(host)) return null
  return host
}

/** Approvals stay on. Missing keys take pane defaults. */
export function mergeConnectPanes(raw?: Partial<ConnectPanes> | null): ConnectPanes {
  const next: ConnectPanes = { ...DEFAULT_CONNECT_PANES }
  if (raw && typeof raw === 'object') {
    for (const key of CONNECT_PANE_KEYS) {
      if (typeof raw[key] === 'boolean') next[key] = raw[key]
    }
  }
  next.approvals = true
  return next
}

function parsePanes(raw: unknown): ConnectPanes {
  if (Array.isArray(raw)) {
    const set = new Set(raw.map((x) => String(x)))
    const fromList: Partial<ConnectPanes> = {}
    for (const key of CONNECT_PANE_KEYS) {
      fromList[key] = set.has(key)
    }
    return mergeConnectPanes(fromList)
  }
  return mergeConnectPanes(asRecord(raw) as Partial<ConnectPanes> | null)
}

function parseDevices(raw: unknown): ConnectDevice[] {
  if (!Array.isArray(raw)) return []
  const out: ConnectDevice[] = []
  for (const row of raw) {
    const o = asRecord(row)
    if (!o) continue
    const id = String(o.id || '').trim()
    if (!id) continue
    const name = String(o.name || 'Phone').trim() || 'Phone'
    const paired =
      o.paired_at == null || o.paired_at === ''
        ? undefined
        : String(o.paired_at)
    out.push({ id, name, paired_at: paired })
  }
  return out
}

function numPort(v: unknown, fallback: number): number {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
  if (!Number.isFinite(n) || n < 0) return fallback
  return Math.floor(n)
}

/** `[host, port]` from the server, `null` for an explicit null, else undefined. */
function parseListening(raw: unknown): ConnectListening | null | undefined {
  if (raw === null) return null
  if (!Array.isArray(raw) || raw.length < 2) return undefined
  const host = String(raw[0] ?? '').trim()
  const port = numPort(raw[1], 0)
  if (!host || port <= 0) return null
  return [host, port]
}

/** "host:port" the phone can reach, or '' when nothing is bound / unknown. */
export function connectListenLabel(st: Pick<ConnectStatus, 'listening' | 'bind_host' | 'bind_port'>): string {
  if (st.listening === null) return ''
  if (Array.isArray(st.listening)) {
    const [host, port] = st.listening
    return port > 0 ? `${host}:${port}` : host
  }
  const host = (st.bind_host || '').trim()
  if (!host) return ''
  return st.bind_port > 0 ? `${host}:${st.bind_port}` : host
}

/**
 * "3m ago" for a device's paired_at. The store writes epoch seconds as a
 * float; ISO strings are accepted too.
 */
export function connectPairedLabel(at?: string | number | null, now = Date.now()): string {
  if (at == null || at === '') return ''
  const n = typeof at === 'number' ? at : Number(at)
  if (Number.isFinite(n)) {
    const ms = n < 1e12 ? n * 1000 : n
    return relativeTime(new Date(ms).toISOString(), now)
  }
  return relativeTime(String(at), now)
}

export function normalizeConnectStatus(
  raw: unknown,
  fallback?: Partial<ConnectStatus>,
): ConnectStatus {
  const o = asRecord(raw) || {}
  const fb = fallback || {}
  const bindHostRaw = String(o.bind_host ?? fb.bind_host ?? '').trim()
  const bind_host = bindHostRaw === '0.0.0.0' || bindHostRaw === '*' ? '' : bindHostRaw
  const addresses = filterConnectAddresses(o.addresses ?? fb.addresses)
  const listening = 'listening' in o ? parseListening(o.listening) : fb.listening
  const qrRaw = o.qr === undefined ? fb.qr : o.qr
  return {
    enabled: Boolean(o.enabled ?? fb.enabled),
    paused: Boolean(o.paused ?? fb.paused),
    bind_host,
    bind_port: numPort(o.bind_port ?? fb.bind_port, 0),
    panes: parsePanes(o.panes ?? fb.panes),
    relay_url: String(o.relay_url ?? fb.relay_url ?? ''),
    devices: parseDevices(o.devices ?? fb.devices),
    addresses,
    qr: typeof qrRaw === 'string' && qrRaw ? qrRaw : qrRaw === null ? null : undefined,
    listening,
  }
}

export async function getConnect(): Promise<ConnectStatus> {
  const raw = await apiFetch<unknown>('/connect')
  return normalizeConnectStatus(raw)
}

export async function putConnect(body: ConnectPutBody): Promise<ConnectStatus> {
  const raw = await apiFetch<unknown>('/connect', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
  const o = asRecord(raw)
  if (o && ('enabled' in o || 'panes' in o || 'devices' in o || 'listening' in o)) {
    // PUT echoes a config snapshot whose `listening` is always null; it does
    // not consult the lifecycle. Leave it unknown so the UI re-reads via GET.
    const { listening: _ignored, ...rest } = o
    return normalizeConnectStatus(rest, body)
  }
  return normalizeConnectStatus(body, body)
}

export async function getConnectAddresses(): Promise<string[]> {
  try {
    const raw = await apiFetch<{ addresses?: string[] }>('/connect/addresses')
    return filterConnectAddresses(raw?.addresses)
  } catch {
    return []
  }
}

export async function startConnectPair(): Promise<ConnectPairStart> {
  const raw = await apiFetch<{ qr?: string; exp?: number }>('/connect/pair/start', {
    method: 'POST',
  })
  return {
    qr: typeof raw?.qr === 'string' ? raw.qr : '',
    exp: typeof raw?.exp === 'number' && Number.isFinite(raw.exp) ? raw.exp : undefined,
  }
}

export async function revokeConnectDevice(id: string): Promise<void> {
  await apiFetch(`/connect/devices/${encodeURIComponent(id)}/revoke`, {
    method: 'POST',
  })
}

function looksLikeConnectStatus(o: Record<string, unknown>): boolean {
  return 'panes' in o || 'devices' in o || 'bind_host' in o || 'listening' in o
}

/**
 * `/connect/pause` and `/connect/resume` answer `{ok, paused}`; `status` is
 * filled only if a server ever returns a full snapshot instead. `ok: false`
 * means the caller should fall back to a full PUT.
 */
export interface ConnectPauseResult {
  ok: boolean
  paused: boolean
  status?: ConnectStatus
}

export function parseConnectPauseResult(raw: unknown, wanted: boolean): ConnectPauseResult {
  const o = asRecord(raw)
  if (!o) return { ok: false, paused: wanted }
  if (looksLikeConnectStatus(o)) {
    const status = normalizeConnectStatus(raw)
    return { ok: true, paused: status.paused, status }
  }
  if (typeof o.paused === 'boolean') {
    return { ok: o.ok !== false, paused: o.paused }
  }
  return { ok: o.ok === true, paused: wanted }
}

export async function pauseConnect(): Promise<ConnectPauseResult> {
  const raw = await apiFetch<unknown>('/connect/pause', { method: 'POST' })
  return parseConnectPauseResult(raw, true)
}

export async function resumeConnect(): Promise<ConnectPauseResult> {
  const raw = await apiFetch<unknown>('/connect/resume', { method: 'POST' })
  return parseConnectPauseResult(raw, false)
}

export interface TailscaleStatus {
  installed: boolean
  running: boolean
  logged_in: boolean
  tailnet_ipv4: string
  version: string
  error: string
}

export interface TailscaleAction {
  status: string
  message: string
  login_url?: string
  msi_path?: string
  installer_url?: string
}

export function parseTailscaleStatus(raw: unknown): TailscaleStatus {
  const o = asRecord(raw) || {}
  return {
    installed: Boolean(o.installed),
    running: Boolean(o.running),
    logged_in: Boolean(o.logged_in),
    tailnet_ipv4: String(o.tailnet_ipv4 ?? ''),
    version: String(o.version ?? ''),
    error: String(o.error ?? ''),
  }
}

export function parseTailscaleAction(raw: unknown): TailscaleAction {
  const o = asRecord(raw) || {}
  return {
    status: String(o.status ?? ''),
    message: String(o.message ?? ''),
    login_url: typeof o.login_url === 'string' ? o.login_url : undefined,
    msi_path: typeof o.msi_path === 'string' ? o.msi_path : undefined,
    installer_url: typeof o.installer_url === 'string' ? o.installer_url : undefined,
  }
}

export async function getTailscaleStatus(): Promise<TailscaleStatus> {
  try {
    const raw = await apiFetch<unknown>('/connect/tailscale/status')
    return parseTailscaleStatus(raw)
  } catch {
    return {
      installed: false,
      running: false,
      logged_in: false,
      tailnet_ipv4: '',
      version: '',
      error: 'Tailscale status unavailable.',
    }
  }
}

export async function installTailscale(): Promise<TailscaleAction> {
  const raw = await apiFetch<unknown>('/connect/tailscale/install', { method: 'POST' })
  return parseTailscaleAction(raw)
}

export async function loginTailscale(): Promise<TailscaleAction> {
  const raw = await apiFetch<unknown>('/connect/tailscale/login', { method: 'POST' })
  return parseTailscaleAction(raw)
}
