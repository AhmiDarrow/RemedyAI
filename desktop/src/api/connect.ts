/** RemedyConnect — phone remote (Tailscale + LAN). */

import { apiFetch } from './client'

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
  listen?: string
}

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

export function normalizeConnectStatus(
  raw: unknown,
  fallback?: Partial<ConnectStatus>,
): ConnectStatus {
  const o = asRecord(raw) || {}
  const fb = fallback || {}
  const bindHostRaw = String(o.bind_host ?? fb.bind_host ?? '').trim()
  const bind_host = bindHostRaw === '0.0.0.0' || bindHostRaw === '*' ? '' : bindHostRaw
  const addresses = filterConnectAddresses(o.addresses ?? fb.addresses)
  const listen =
    typeof o.listen === 'string' && o.listen.trim()
      ? o.listen.trim()
      : typeof fb.listen === 'string'
        ? fb.listen
        : undefined
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
    listen,
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
  if (o && ('enabled' in o || 'panes' in o || 'devices' in o || 'listen' in o)) {
    return normalizeConnectStatus(raw, body)
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
  return 'panes' in o || 'devices' in o || 'bind_host' in o || 'listen' in o
}

export async function pauseConnect(): Promise<ConnectStatus | void> {
  const raw = await apiFetch<unknown>('/connect/pause', { method: 'POST' })
  const o = asRecord(raw)
  if (o && looksLikeConnectStatus(o)) return normalizeConnectStatus(raw)
}

export async function resumeConnect(): Promise<ConnectStatus | void> {
  const raw = await apiFetch<unknown>('/connect/resume', { method: 'POST' })
  const o = asRecord(raw)
  if (o && looksLikeConnectStatus(o)) return normalizeConnectStatus(raw)
}
