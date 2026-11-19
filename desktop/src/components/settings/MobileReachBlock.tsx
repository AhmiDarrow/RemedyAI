/**
 * Settings → Connect → "Reach this PC from mobile data".
 *
 * Opt-in. When on, this PC runs its own iroh endpoint on a fixed UDP port and
 * asks the home router to open it (UPnP / NAT-PMP / PCP). No account, no VPN
 * app, no third-party relay by default. The self-test says in plain words
 * whether a phone on mobile data can reach this PC, and what to do if not.
 */

import { useState } from 'react'
import {
  type MobileReach,
  mobileVerdictTone,
  putMobileReach,
  testMobileReach,
} from '../../api/connect'
import { FormActionButton, FormHint, FormInput, FormLabel, FormNotice, FormSelect, FormToggle } from './formUi'

export function MobileReachBlock({
  mobile,
  disabled,
  onChanged,
}: {
  mobile?: MobileReach
  disabled?: boolean
  onChanged: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [portDraft, setPortDraft] = useState('')
  const [relayDraft, setRelayDraft] = useState('')
  const [advanced, setAdvanced] = useState(false)

  if (!mobile) return null
  const st = mobile.status

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setErr('')
    try {
      await fn()
      await onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const tone = mobileVerdictTone(st.verdict)
  return (
    <div data-testid="mobile-reach">
      <FormLabel className="mt-3">Reach this PC from mobile data</FormLabel>
      <FormToggle
        checked={mobile.enabled}
        disabled={disabled || busy}
        onChange={(on) => void run(() => putMobileReach({ mobile_reach: on }))}
        label="Let my paired phone reach this PC from anywhere"
        description="No account and no VPN app. This PC opens one UDP port on your router; everything stays end-to-end encrypted."
      />
      {mobile.enabled ? (
        <>
          {st.verdict === 'checking' || !st.message ? (
            <FormHint>Checking whether your phone can reach this PC…</FormHint>
          ) : (
            <FormNotice tone={tone === 'ok' ? 'accent' : tone === 'warn' ? 'warn' : 'muted'}>{st.message}</FormNotice>
          )}
          {st.steps.length ? (
            <ol className="text-xs mb-2 pl-5 list-decimal" style={{ color: 'var(--text-secondary)' }}>
              {st.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          ) : null}
          <FormToggle
            checked={mobile.punch}
            disabled={disabled || busy}
            onChange={(on) => void run(() => putMobileReach({ mobile_punch: on }))}
            label="Get through carrier-grade NAT"
            description="If your provider shares one address between many homes, try a direct connection anyway. Uses public MQTT brokers to swap encrypted addresses; they never see your chats or addresses. Re-pair the phone after changing this."
          />
          <FormActionButton disabled={busy} onClick={() => void run(() => testMobileReach())}>
            {busy ? 'Testing…' : 'Test again'}
          </FormActionButton>
          <FormHint>
            Pair (or re-pair) the phone after turning this on so the QR carries this PC's address.
            If your provider uses carrier-grade NAT, LAN, Tailscale and your own relay still work.
          </FormHint>
          <FormToggle
            checked={advanced}
            onChange={setAdvanced}
            label="Advanced"
          />
          {advanced ? (
            <>
              <FormLabel htmlFor="mobile-port">UDP port</FormLabel>
              <FormInput
                id="mobile-port"
                value={portDraft || String(mobile.port)}
                onChange={setPortDraft}
              />
              {portDraft && portDraft !== String(mobile.port) ? (
                <FormActionButton
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await putMobileReach({ mobile_port: Number(portDraft) })
                      setPortDraft('')
                    })
                  }
                >
                  Save port
                </FormActionButton>
              ) : null}
              <FormLabel htmlFor="mobile-relay-mode">Relay when a direct path fails</FormLabel>
              <FormSelect
                id="mobile-relay-mode"
                size="sm"
                value={mobile.relay_mode}
                onChange={(v) =>
                  void run(() => putMobileReach({ mobile_relay_mode: v as 'off' | 'custom' | 'n0' }))
                }
                options={[
                  { value: 'off', label: 'None — direct only (default)' },
                  { value: 'custom', label: 'My own iroh-relay' },
                  { value: 'n0', label: "n0's public relays (no guarantee of service)" },
                ]}
              />
              {mobile.relay_mode === 'custom' ? (
                <>
                  <FormInput
                    id="mobile-relay-url"
                    value={relayDraft || mobile.relay_url}
                    onChange={setRelayDraft}
                    placeholder="https://relay.example.com"
                  />
                  {relayDraft && relayDraft !== mobile.relay_url ? (
                    <FormActionButton
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await putMobileReach({ mobile_relay_url: relayDraft.trim() })
                          setRelayDraft('')
                        })
                      }
                    >
                      Save relay
                    </FormActionButton>
                  ) : null}
                </>
              ) : null}
              <FormHint>
                A relay only passes encrypted bytes; it sees IP addresses and timing, never your chats.
              </FormHint>
            </>
          ) : null}
        </>
      ) : null}
      {err ? <FormNotice tone="warn">{err}</FormNotice> : null}
    </div>
  )
}
