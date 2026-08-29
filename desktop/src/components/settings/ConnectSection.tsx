/** Settings → Connect — phone on this network. Own API, like Phone. */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  CONNECT_PANE_KEYS,
  CONNECT_PANE_LABELS,
  type ConnectPaneKey,
  type ConnectStatus,
  filterConnectAddresses,
  isConnectLoopbackBind,
  getConnect,
  getConnectAddresses,
  mergeConnectPanes,
  pauseConnect,
  putConnect,
  resumeConnect,
  revokeConnectDevice,
  startConnectPair,
} from '../../api/connect'
import { relativeTime } from '../../utils/relativeTime'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormHint,
  FormInput,
  FormLabel,
  FormNotice,
  FormSelect,
  FormToggle,
} from './formUi'

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

function listenLabel(st: ConnectStatus): string {
  if (st.listen && st.listen.trim()) return st.listen.trim()
  const host = st.bind_host.trim()
  if (!host) return ''
  return st.bind_port > 0 ? `${host}:${st.bind_port}` : host
}

function pairedLabel(at?: string): string {
  if (!at) return ''
  if (/^\d+$/.test(at)) {
    const n = Number(at)
    const ms = n < 1e12 ? n * 1000 : n
    return relativeTime(new Date(ms).toISOString())
  }
  return relativeTime(at)
}

function expLabel(exp?: number): string {
  if (exp == null || !Number.isFinite(exp)) return ''
  const ms = exp < 1e12 ? exp * 1000 : exp
  const sec = Math.max(0, Math.round((ms - Date.now()) / 1000))
  if (sec <= 0) return 'This code has expired. Pair again.'
  if (sec < 60) return `Expires in ${sec}s`
  return `Expires in ${Math.ceil(sec / 60)} min`
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
      return true
    } catch {
      return false
    }
  }
}

function PairModal({
  qr,
  exp,
  onClose,
}: {
  qr: string
  exp?: number
  onClose: () => void
}): ReactNode {
  const [copied, setCopied] = useState(false)
  const image = qr.startsWith('data:image') || /\.(png|svg)(\?|$)/i.test(qr)

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="connect-pair-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="ui-surface w-full max-w-sm p-5" style={{ color: 'var(--text-primary)' }}>
        <div id="connect-pair-title" className="font-semibold text-sm mb-1">
          Pair phone
        </div>
        <FormHint>
          Scan or copy this code on the phone. It is not a password — revoke the
          phone anytime below.
        </FormHint>
        {image ? (
          <img
            src={qr}
            alt="Pairing code"
            className="mx-auto mb-2 rounded-lg"
            style={{ maxWidth: '12rem', background: '#fff', padding: '0.5rem' }}
          />
        ) : (
          <pre
            className="font-mono text-[11px] leading-snug rounded-lg px-2.5 py-2 mb-2 overflow-x-auto whitespace-pre-wrap break-all"
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          >
            {qr}
          </pre>
        )}
        {expLabel(exp) ? <FormHint>{expLabel(exp)}</FormHint> : null}
        <div className="flex flex-wrap gap-2">
          <FormActionButton
            variant="primary"
            onClick={async () => {
              const ok = await copyText(qr)
              setCopied(ok)
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </FormActionButton>
          <FormActionButton onClick={onClose}>Done</FormActionButton>
        </div>
      </div>
    </div>
  )
}

export function ConnectSection({
  sectionProps,
}: {
  sectionProps: SectionProps
}): ReactNode {
  const [st, setSt] = useState<ConnectStatus | null>(null)
  const [addrs, setAddrs] = useState<string[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [relayDraft, setRelayDraft] = useState('')
  const [pair, setPair] = useState<{ qr: string; exp?: number } | null>(null)
  const [available, setAvailable] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [status, extra] = await Promise.all([
        getConnect(),
        getConnectAddresses(),
      ])
      setSt(status)
      setRelayDraft(status.relay_url)
      setAddrs(
        filterConnectAddresses([...(status.addresses || []), ...extra]),
      )
      setAvailable(true)
    } catch {
      setAvailable(false)
      setSt(null)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const persist = async (patch: Partial<ConnectStatus>) => {
    const base = st
    if (!base && patch.enabled !== true) return
    const panes = mergeConnectPanes(patch.panes ?? base?.panes)
    const pickedHost =
      patch.bind_host
      ?? base?.bind_host
      ?? addrs.find((ip) => !isConnectLoopbackBind(ip))
      ?? addrs[0]
      ?? ''
    const body = {
      enabled: patch.enabled ?? base?.enabled ?? false,
      bind_host: pickedHost,
      bind_port: patch.bind_port ?? base?.bind_port ?? 0,
      paused: patch.paused ?? base?.paused ?? false,
      panes,
      relay_url: patch.relay_url ?? base?.relay_url ?? '',
    }
    setBusy(true)
    setMsg('')
    try {
      const next = await putConnect(body)
      setSt(next)
      setRelayDraft(next.relay_url)
      if (next.addresses?.length) {
        setAddrs(filterConnectAddresses([...(next.addresses || []), ...addrs]))
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const hostOptions = useMemo(() => {
    const list = [...addrs]
    const cur = st?.bind_host?.trim() || ''
    if (cur && !list.includes(cur) && filterConnectAddresses([cur]).length) {
      list.unshift(cur)
    }
    return list.map((ip) => ({ value: ip, label: ip }))
  }, [addrs, st?.bind_host])

  const listen = st ? listenLabel(st) : ''

  return (
    <SettingsSection
      {...sectionProps}
      title="Connect"
      summary="Use this PC from a paired phone — Wi‑Fi or mobile data"
    >
      <FormHint>
        Let a paired phone drive this PC. Same Wi‑Fi works with no extra setup.
        For mobile data, run an owner relay (below). Off by default. Approvals
        stay on. No Tailscale.
      </FormHint>
      {!available ? (
        <FormNotice tone="muted">
          Connect is not available on this computer yet.
        </FormNotice>
      ) : null}
      <FormToggle
        checked={Boolean(st?.enabled)}
        disabled={busy || !available}
        onChange={(on) => void persist({ enabled: on })}
        label="Enable Connect"
        description="Share this Remedy with a paired phone on your network."
      />
      {st?.enabled ? (
        <>
          <FormLabel>This computer&apos;s address</FormLabel>
          {hostOptions.length ? (
            <FormSelect
              value={
                st.bind_host && hostOptions.some((o) => o.value === st.bind_host)
                  ? st.bind_host
                  : hostOptions[0]!.value
              }
              onChange={(v) => void persist({ bind_host: v })}
              options={hostOptions}
              disabled={busy}
            />
          ) : (
            <FormHint>No IPv4 address yet — connect this PC to the network.</FormHint>
          )}
          {isConnectLoopbackBind(st.bind_host) ? (
            <FormNotice tone="warn">
              127.0.0.1 is only reachable on this computer. Pick a LAN address so the
              phone can connect.
            </FormNotice>
          ) : null}
          <FormHint>
            {st.paused
              ? 'Remote is paused. Phones stay paired but cannot use this PC until you resume.'
              : listen
                ? `Listening at ${listen}`
                : 'Pick an address so the phone can find this PC.'}
          </FormHint>

          <FormLabel>Remote shows</FormLabel>
          {CONNECT_PANE_KEYS.map((key: ConnectPaneKey) => {
            const locked = key === 'approvals'
            return (
              <FormToggle
                key={key}
                checked={locked ? true : Boolean(st.panes[key])}
                disabled={busy || locked}
                onChange={(on) => {
                  if (locked) return
                  void persist({ panes: mergeConnectPanes({ ...st.panes, [key]: on }) })
                }}
                label={CONNECT_PANE_LABELS[key]}
                description={
                  locked ? 'Always on — the phone can Approve, Deny, and Stop.' : undefined
                }
              />
            )
          })}

          <FormActionButton
            variant="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              setMsg('')
              try {
                const r = await startConnectPair()
                if (!r.qr) {
                  setMsg('Could not start pairing.')
                  return
                }
                setPair(r)
              } catch (err) {
                setMsg(err instanceof Error ? err.message : String(err))
              } finally {
                setBusy(false)
              }
            }}
          >
            Pair phone
          </FormActionButton>
          <FormHint>Shows a code the phone scans. Revoke anytime.</FormHint>

          {st.devices.length ? (
            <div className="space-y-1 mb-2">
              {st.devices.map((d) => (
                <div
                  key={d.id}
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5"
                  style={{
                    background: 'var(--bg-tertiary)',
                    border: '1px solid var(--border)',
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                      {d.name}
                    </span>
                    {pairedLabel(d.paired_at) ? (
                      <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        Paired {pairedLabel(d.paired_at)}
                      </span>
                    ) : null}
                  </span>
                  <FormActionButton
                    variant="danger"
                    disabled={busy}
                    className="flex-shrink-0 mb-0"
                    onClick={async () => {
                      setBusy(true)
                      setMsg('')
                      try {
                        await revokeConnectDevice(d.id)
                        await refresh()
                      } catch (err) {
                        setMsg(err instanceof Error ? err.message : String(err))
                      } finally {
                        setBusy(false)
                      }
                    }}
                  >
                    Revoke
                  </FormActionButton>
                </div>
              ))}
            </div>
          ) : (
            <FormHint>No phones paired yet.</FormHint>
          )}

          <FormToggle
            checked={Boolean(st.paused)}
            disabled={busy}
            onChange={async (on) => {
              setBusy(true)
              setMsg('')
              try {
                const next = on ? await pauseConnect() : await resumeConnect()
                if (next) setSt({ ...next, paused: on })
                else await persist({ paused: on })
              } catch {
                await persist({ paused: on })
              } finally {
                setBusy(false)
              }
            }}
            label="Pause remote"
            description="Paired phones stay listed but cannot use this PC until you resume."
          />

          <FormLabel htmlFor="connect-relay-url">Owner relay (for mobile data)</FormLabel>
          <FormInput
            id="connect-relay-url"
            value={relayDraft}
            onChange={setRelayDraft}
            placeholder="203.0.113.10:7402"
            disabled={busy}
            spellCheck={false}
          />
          <FormHint>
            A machine you control that both this PC and the phone can reach.
            Run <code>remedy connect-relay --host THAT_IPV4 --port 7402</code> on
            it, then paste host:port here. The relay only forwards encrypted
            bytes — it cannot read chats. Leave empty for same-Wi‑Fi only.
            Not HTTP, not Tailscale.
          </FormHint>
          {relayDraft !== (st.relay_url || '') ? (
            <FormActionButton
              disabled={busy}
              onClick={() => void persist({ relay_url: relayDraft.trim() })}
            >
              Save relay URL
            </FormActionButton>
          ) : null}
        </>
      ) : null}
      {msg ? <FormNotice tone="error">{msg}</FormNotice> : null}
      {pair ? (
        <PairModal qr={pair.qr} exp={pair.exp} onClose={() => setPair(null)} />
      ) : null}
    </SettingsSection>
  )
}
