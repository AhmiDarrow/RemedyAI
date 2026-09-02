/** Settings → Connect — phone on this network. Own API, like Phone. */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  CONNECT_PANE_KEYS,
  CONNECT_PANE_LABELS,
  type ConnectPaneKey,
  type ConnectStatus,
  type TailscaleStatus,
  connectListenLabel,
  connectPairedLabel,
  filterConnectAddresses,
  getConnect,
  getConnectAddresses,
  getTailscaleStatus,
  installTailscale,
  loginTailscale,
  mergeConnectPanes,
  pauseConnect,
  putConnect,
  resumeConnect,
  revokeConnectDevice,
  startConnectPair,
} from '../../api/connect'
import { openExternalUrl } from '../../api/auth'
import { tauriInvoke } from '../../api/tauri'
import {
  CONNECT_LOOPBACK_BIND_WARNING,
  isConnectLoopbackHost,
  preferredConnectBindHost,
} from '../../utils/connectMode'
import QRCode from 'qrcode'
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

function expLabel(exp: number | undefined, now: number): string {
  if (exp == null || !Number.isFinite(exp)) return ''
  const ms = exp < 1e12 ? exp * 1000 : exp
  const sec = Math.max(0, Math.round((ms - now) / 1000))
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
  const [qrUrl, setQrUrl] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const image = qr.startsWith('data:image') || /\.(png|svg)(\?|$)/i.test(qr)
  const expires = expLabel(exp, now)

  // Tick once a second so "Expires in Ns" counts down and flips to expired.
  useEffect(() => {
    if (exp == null) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [exp])

  // The backend hands us the pairing text. Turn it into a scannable QR here
  // (client-side) so the phone can scan instead of copy/paste.
  useEffect(() => {
    if (image) return
    let alive = true
    QRCode.toDataURL(qr, { width: 480, margin: 1, errorCorrectionLevel: 'M' })
      .then((url: string) => {
        if (alive) setQrUrl(url)
      })
      .catch(() => {
        if (alive) setQrUrl(null)
      })
    return () => {
      alive = false
    }
  }, [qr, image])

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
          Scan this QR from the RemedyConnect phone app (Tailscale on both
          devices, signed into the same account — works on Wi-Fi and mobile
          data), or copy the code and paste it there. It is not a password —
          revoke the phone anytime below.
        </FormHint>
        {image || qrUrl ? (
          <img
            src={image ? qr : (qrUrl ?? undefined)}
            alt="Pairing QR code"
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
        {expires ? <FormHint>{expires}</FormHint> : null}
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
  const [ts, setTs] = useState<TailscaleStatus | null>(null)
  const [tsBusy, setTsBusy] = useState(false)
  const [tsMsg, setTsMsg] = useState('')

  const refreshTs = useCallback(async () => {
    setTsBusy(true)
    setTsMsg('')
    try {
      const s = await getTailscaleStatus()
      setTs(s)
    } catch {
      setTs(null)
    } finally {
      setTsBusy(false)
    }
  }, [])

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

  useEffect(() => {
    if (st?.enabled) void refreshTs()
    else setTs(null)
  }, [st?.enabled, refreshTs])

  const persist = async (patch: Partial<ConnectStatus>) => {
    const base = st
    if (!base && patch.enabled !== true) return
    const panes = mergeConnectPanes(patch.panes ?? base?.panes)
    const existingHost = patch.bind_host !== undefined ? patch.bind_host : base?.bind_host
    const pickedHost = preferredConnectBindHost(addrs, existingHost)
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
      const saved = await putConnect(body)
      // PUT echoes config only; GET carries what the listener actually bound.
      const next = await getConnect().catch(() => saved)
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
      list.push(cur)
    }
    const ordered = [
      ...list.filter((ip) => !isConnectLoopbackHost(ip)),
      ...list.filter((ip) => isConnectLoopbackHost(ip)),
    ]
    return ordered.map((ip) => ({
      value: ip,
      label: isConnectLoopbackHost(ip) ? `${ip} — this computer only` : ip,
    }))
  }, [addrs, st?.bind_host])

  const selectedHost = preferredConnectBindHost(
    hostOptions.map((o) => o.value),
    st?.bind_host,
  )

  const listen = st ? connectListenLabel(st) : ''
  const notListening = Boolean(st?.enabled) && st?.listening === null

  return (
    <SettingsSection
      {...sectionProps}
      title="Connect"
      summary="Use this PC from a paired phone — Wi‑Fi or mobile data"
    >
      <FormHint>
        Let a paired phone drive this PC. Same Wi‑Fi works with no extra setup;
        add the free Tailscale app for mobile data anywhere (install it below,
        on this PC and on your phone — same account). Approvals stay on.
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
                selectedHost && hostOptions.some((o) => o.value === selectedHost)
                  ? selectedHost
                  : hostOptions[0]!.value
              }
              onChange={(v) => void persist({ bind_host: v })}
              options={hostOptions}
              disabled={busy}
            />
          ) : (
            <FormHint>No IPv4 address yet — connect this PC to the network.</FormHint>
          )}
          {isConnectLoopbackHost(st.bind_host || selectedHost) ? (
            <FormNotice tone="warn">{CONNECT_LOOPBACK_BIND_WARNING}</FormNotice>
          ) : null}
          {notListening && !st.paused ? (
            <FormNotice tone="warn">
              Not listening. Connect is on but nothing is bound
              {st.bind_host ? ` at ${st.bind_host}${st.bind_port > 0 ? `:${st.bind_port}` : ''}` : ''}
              {msg ? ` — ${msg}` : ''}. Pick another address or restart the server below.
            </FormNotice>
          ) : (
            <FormHint>
              {st.paused
                ? 'Remote is paused. Phones stay paired but cannot use this PC until you resume.'
                : listen
                  ? `Listening at ${listen}`
                  : 'Pick an address so the phone can find this PC.'}
            </FormHint>
          )}

          <FormLabel>Tailscale (mobile data)</FormLabel>
          {ts ? (
            ts.logged_in && ts.tailnet_ipv4 ? (
              <FormNotice tone="muted">
                Connected — tailnet {ts.tailnet_ipv4}
                {ts.version ? ` (Tailscale ${ts.version})` : ''}. The pairing QR
                carries this address, so the phone works on Wi‑Fi and mobile data.
              </FormNotice>
            ) : ts.installed ? (
              <FormNotice tone="warn">
                Tailscale is installed but {ts.running ? 'not signed in' : 'not running'}.
                {ts.error ? ` ${ts.error}` : ''}
              </FormNotice>
            ) : (
              <FormNotice tone="warn">
                Tailscale is not installed. Install it free so your phone works
                on mobile data, not just your home Wi‑Fi.
              </FormNotice>
            )
          ) : (
            <FormHint>Checking Tailscale…</FormHint>
          )}
          {ts && !ts.installed ? (
            <FormActionButton
              variant="primary"
              disabled={tsBusy}
              onClick={async () => {
                setTsBusy(true)
                setTsMsg('')
                try {
                  const r = await installTailscale()
                  setTsMsg(r.message)
                  setTs(await getTailscaleStatus())
                } catch (err) {
                  setTsMsg(err instanceof Error ? err.message : String(err))
                } finally {
                  setTsBusy(false)
                }
              }}
            >
              Install Tailscale (free)
            </FormActionButton>
          ) : null}
          {ts && ts.installed && !ts.logged_in ? (
            <FormActionButton
              variant="primary"
              disabled={tsBusy}
              onClick={async () => {
                setTsBusy(true)
                setTsMsg('')
                try {
                  const r = await loginTailscale()
                  setTsMsg(r.message)
                  if (r.login_url) {
                    await openExternalUrl(r.login_url)
                  }
                  setTs(await getTailscaleStatus())
                } catch (err) {
                  setTsMsg(err instanceof Error ? err.message : String(err))
                } finally {
                  setTsBusy(false)
                }
              }}
            >
              Sign in to Tailscale
            </FormActionButton>
          ) : null}
          {tsMsg ? <FormHint>{tsMsg}</FormHint> : null}
          <FormHint>
            On the phone: install the free Tailscale app from the Play Store and
            sign into the same account as this PC. Then scan the pairing QR —
            it includes the tailnet address, so RemedyConnect works anywhere.
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
                    {connectPairedLabel(d.paired_at) ? (
                      <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        Paired {connectPairedLabel(d.paired_at)}
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
                // /pause and /resume answer {ok, paused}; that is the whole
                // change, so no follow-up PUT unless the server balked.
                const r = on ? await pauseConnect() : await resumeConnect()
                if (r.status) setSt({ ...r.status, paused: r.paused })
                else if (r.ok) setSt((prev) => (prev ? { ...prev, paused: r.paused } : prev))
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

          <FormActionButton
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              setMsg('')
              try {
                await tauriInvoke('restart_server')
                setMsg('Server restarted.')
              } catch (err) {
                setMsg(err instanceof Error ? err.message : String(err))
              } finally {
                setBusy(false)
              }
            }}
          >
            Restart server
          </FormActionButton>
          <FormHint>
            Kills and respawns the Remedy server. Use this when the server is
            hung or unreachable — Reconnect alone cannot help if the server
            itself is down.
          </FormHint>

          <FormLabel htmlFor="connect-relay-url">Owner relay (optional — for lower latency)</FormLabel>
          <FormInput
            id="connect-relay-url"
            value={relayDraft}
            onChange={setRelayDraft}
            placeholder="203.0.113.10:7402"
            disabled={busy}
            spellCheck={false}
          />
          <FormHint>
            Mobile data already works out of the box via the automatic public
            rendezvous. Only fill this in if you host your own relay for lower
            latency: run <code>remedy connect-relay --host THAT_IPV4 --port 7402</code>
            on a machine you control, then paste host:port here. The relay only
            forwards encrypted bytes — it cannot read chats. Not HTTP, not Tailscale.
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
        <PairModal
          qr={pair.qr}
          exp={pair.exp}
          onClose={() => {
            setPair(null)
            // A phone may have paired while the QR was up; pick up the new row.
            void refresh()
          }}
        />
      ) : null}
    </SettingsSection>
  )
}
