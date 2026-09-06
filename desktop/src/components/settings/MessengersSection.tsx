/** Settings → Messengers — nested expandables (one row per platform). */

import { useCallback, useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { openExternalUrl } from '../../api/auth'
import {
  SIGNAL_JAVA_DOWNLOAD_URL,
  ensureManagedSignalCLI,
  getMessengerTunnelStatus,
  startMessengerTunnel,
  stopMessengerTunnel,
  type MessengerInfo,
  type MessengerTunnelStatus,
} from '../../api/settings'
import { SettingsSection } from '../SettingsSection'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'
import { FormSegmented } from './formUi'

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

export interface MessengersSectionProps {
  sectionProps: SectionProps
  messengers: MessengerInfo[]
  messengerDrafts: MessengerDraftMap
  setMessengerDrafts: Dispatch<SetStateAction<MessengerDraftMap>>
  /** Refresh settings/messengers after tunnel or signal install changes health. */
  onMessengersRefresh?: () => void | Promise<void>
}

const STATUS_COLOR: Record<string, string> = {
  ready: 'var(--success)',
  needs_setup: 'var(--warning, #c9a227)',
  planned: 'var(--text-muted)',
}

function statusLabel(status: string): string {
  if (status === 'needs_setup') return 'needs setup'
  return status
}

export function MessengersSection({
  sectionProps,
  messengers,
  messengerDrafts,
  setMessengerDrafts,
  onMessengersRefresh,
}: MessengersSectionProps): ReactNode {
  /** Which messenger rows are expanded (collapsed by default). */
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [tunnel, setTunnel] = useState<MessengerTunnelStatus | null>(null)
  const [tunnelBusy, setTunnelBusy] = useState(false)
  const [tunnelMsg, setTunnelMsg] = useState('')
  const [tunnelMode, setTunnelMode] = useState<'quick' | 'named'>('quick')
  const [namedToken, setNamedToken] = useState('')
  const [namedPublicURL, setNamedPublicURL] = useState('')
  const [signalBusy, setSignalBusy] = useState(false)
  const [signalMsg, setSignalMsg] = useState('')

  const refreshTunnel = useCallback(async () => {
    try {
      const st = await getMessengerTunnelStatus()
      setTunnel(st)
      if (st.mode === 'named' || st.mode === 'quick') {
        setTunnelMode(st.mode)
      }
      if (st.mode === 'named' && st.public_url) {
        setNamedPublicURL(st.public_url)
      }
    } catch {
      setTunnel(null)
    }
  }, [])

  useEffect(() => {
    void refreshTunnel()
  }, [refreshTunnel])

  const enabledCount = useMemo(
    () =>
      messengers.filter((m) => {
        const d = messengerDrafts[m.id]
        return d?.enabled !== undefined ? Boolean(d.enabled) : Boolean(m.enabled)
      }).length,
    [messengers, messengerDrafts],
  )

  const patch = (id: string, key: string, value: string | boolean) => {
    setMessengerDrafts((prev) => ({
      ...prev,
      [id]: { ...(prev[id] || {}), [key]: value },
    }))
  }

  const toggleRow = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const onExposeWebhooks = async () => {
    setTunnelBusy(true)
    setTunnelMsg('')
    try {
      const st =
        tunnelMode === 'named'
          ? await startMessengerTunnel({
              mode: 'named',
              token: namedToken.trim(),
              public_base_url: namedPublicURL.trim(),
            })
          : await startMessengerTunnel({ mode: 'quick' })
      setTunnel(st)
      setTunnelMsg(
        st.public_url
          ? `Public HTTPS ready: ${st.public_url}`
          : st.error || 'Tunnel started.',
      )
      await onMessengersRefresh?.()
    } catch (err) {
      setTunnelMsg(err instanceof Error ? err.message : String(err))
      await refreshTunnel()
    } finally {
      setTunnelBusy(false)
    }
  }

  const onStopTunnel = async () => {
    setTunnelBusy(true)
    setTunnelMsg('')
    try {
      const st = await stopMessengerTunnel()
      setTunnel(st)
      setTunnelMsg('Tunnel stopped. Webhook messengers need a public HTTPS URL again.')
      await onMessengersRefresh?.()
    } catch (err) {
      setTunnelMsg(err instanceof Error ? err.message : String(err))
      await refreshTunnel()
    } finally {
      setTunnelBusy(false)
    }
  }

  const onInstallSignal = async () => {
    setSignalBusy(true)
    setSignalMsg('')
    try {
      const res = await ensureManagedSignalCLI()
      const path = res.cli_path || ''
      if (path) {
        patch('signal', 'cli_path', path)
      }
      const needsJavaHint = Boolean(res.needs_java) && res.java_ok === false
      setSignalMsg(
        res.hint ||
          (needsJavaHint
            ? 'Installed. Install Java 21+ (Temurin) and restart Remedy, then set the account number.'
            : 'Installed. Set the Signal account phone number.'),
      )
      await onMessengersRefresh?.()
    } catch (err) {
      setSignalMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setSignalBusy(false)
    }
  }

  const tunnelRunning = Boolean(tunnel?.running)
  const tunnelConfigured = Boolean(tunnel?.env_configured || tunnel?.public_url)
  const namedReady =
    tunnelMode !== 'named' ||
    (namedToken.trim().length > 0 && namedPublicURL.trim().toLowerCase().startsWith('https://'))
  const startLabel =
    tunnelBusy
      ? 'Starting…'
      : tunnelMode === 'named'
        ? 'Start named tunnel'
        : 'Expose messenger webhooks'

  return (
    <SettingsSection
      {...sectionProps}
      summary={
        enabledCount > 0
          ? `${enabledCount} connected · expand to configure`
          : sectionProps.summary || 'Telegram, Discord, WhatsApp…'
      }
    >
      <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
        Expand a messenger to set tokens and options. Chats show up in the session list
        (realtime). Empty secret fields leave the current token unchanged.
      </div>

      <div
        className="rounded px-2 py-1.5 mb-2 space-y-1"
        style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[11px] font-medium" style={{ color: 'var(--text-primary)' }}>
              Webhook tunnel
            </div>
            <div className="text-[9px] leading-snug" style={{ color: 'var(--text-muted)' }}>
              WhatsApp, Teams, and Google Chat need a public HTTPS address. Remedy stays on
              this computer; the tunnel only forwards webhooks.
            </div>
          </div>
          <span
            className="text-[9px] flex-shrink-0"
            style={{ color: tunnelRunning || tunnelConfigured ? 'var(--success)' : 'var(--text-muted)' }}
          >
            {tunnelRunning ? 'running' : tunnelConfigured ? 'URL set' : 'not exposed'}
          </span>
        </div>
        {tunnel?.public_url && (
          <div className="text-[9px] truncate" style={{ color: 'var(--text-secondary)' }}>
            {tunnel.public_url}
          </div>
        )}
        {!tunnelRunning && (
          <FormSegmented
            value={tunnelMode}
            disabled={tunnelBusy}
            className="mb-1"
            options={[
              { id: 'quick', label: 'Quick', title: 'Temporary trycloudflare.com URL' },
              { id: 'named', label: 'Named', title: 'Your Cloudflare tunnel token + hostname' },
            ]}
            onChange={setTunnelMode}
          />
        )}
        {!tunnelRunning && tunnelMode === 'named' && (
          <div className="space-y-1">
            <div className="text-[9px] leading-snug" style={{ color: 'var(--text-muted)' }}>
              Paste the Cloudflare tunnel token and the HTTPS hostname you published for this
              computer.
            </div>
            <input
              type="password"
              value={namedToken}
              placeholder="Cloudflare tunnel token"
              onChange={(e) => setNamedToken(e.target.value)}
              className="ui-input"
              autoComplete="off"
              disabled={tunnelBusy}
            />
            <input
              type="url"
              value={namedPublicURL}
              placeholder="https://messengers.example.com"
              onChange={(e) => setNamedPublicURL(e.target.value)}
              className="ui-input"
              autoComplete="off"
              disabled={tunnelBusy}
            />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {!tunnelRunning ? (
            <button
              type="button"
              className="text-[10px] px-2 py-0.5 rounded"
              style={{
                background: 'var(--accent)',
                color: 'var(--accent-fg, #fff)',
                opacity: tunnelBusy || !namedReady ? 0.6 : 1,
              }}
              disabled={tunnelBusy || !namedReady}
              onClick={() => void onExposeWebhooks()}
            >
              {startLabel}
            </button>
          ) : (
            <button
              type="button"
              className="text-[10px] underline"
              style={{ color: 'var(--accent)' }}
              disabled={tunnelBusy}
              onClick={() => void onStopTunnel()}
            >
              Stop tunnel
            </button>
          )}
        </div>
        {tunnelMsg && (
          <div className="text-[9px] leading-snug" style={{ color: 'var(--text-muted)' }}>
            {tunnelMsg}
          </div>
        )}
      </div>

      {messengers.length === 0 && (
        <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          Catalog unavailable — save Settings once the server is up.
        </div>
      )}

      <div className="space-y-1">
        {messengers.map((m) => {
          const draft = messengerDrafts[m.id] || { enabled: m.enabled }
          const enabled = Boolean(draft.enabled)
          const open = Boolean(expanded[m.id])
          const statusColor = STATUS_COLOR[m.status] || 'var(--text-muted)'
          const signalNeedsInstall =
            m.id === 'signal' && m.status === 'needs_setup' && m.health?.cli_ok === false
          const signalNeedsJava =
            m.id === 'signal' &&
            m.health?.cli_ok === true &&
            m.health?.needs_java === true &&
            m.health?.java_ok === false

          return (
            <div
              key={m.id}
              className="rounded overflow-hidden"
              style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
            >
              {/* Compact header — expand + enable without opening all fields */}
              <div className="flex items-center gap-1 px-1.5 py-1">
                <button
                  type="button"
                  onClick={() => toggleRow(m.id)}
                  className="flex items-center gap-1.5 flex-1 min-w-0 text-left py-0.5"
                  aria-expanded={open}
                >
                  <span
                    className="inline-flex w-3.5 justify-center text-[9px] flex-shrink-0"
                    style={{ color: 'var(--text-muted)' }}
                    aria-hidden
                  >
                    {open ? '▼' : '▶'}
                  </span>
                  <span
                    className="text-[11px] font-medium truncate"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {m.name}
                  </span>
                  <span className="text-[9px] flex-shrink-0" style={{ color: statusColor }}>
                    {statusLabel(m.status)}
                    {m.token_set ? ' · key' : ''}
                  </span>
                </button>
                <label
                  className="flex items-center gap-1 cursor-pointer flex-shrink-0 pl-1"
                  title={enabled ? 'Disable' : 'Enable'}
                  onClick={(e) => e.stopPropagation()}
                >
                  <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
                    On
                  </span>
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => {
                      const on = e.target.checked
                      patch(m.id, 'enabled', on)
                      // Auto-expand when enabling so fields are one click away
                      if (on) setExpanded((prev) => ({ ...prev, [m.id]: true }))
                    }}
                    style={{ accentColor: 'var(--accent)' }}
                  />
                </label>
              </div>

              {open && (
                <div
                  className="px-2 pb-2 pt-0.5 space-y-1.5"
                  style={{ borderTop: '1px solid var(--border)' }}
                >
                  {m.description && (
                    <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                      {m.description}
                    </div>
                  )}
                  {m.status_reason && (
                    <div
                      className="text-[10px] leading-snug"
                      style={{ color: STATUS_COLOR[m.status] || 'var(--text-muted)' }}
                    >
                      {m.status_reason}
                    </div>
                  )}
                  {m.docs_url && (
                    <button
                      type="button"
                      className="text-[10px] underline"
                      style={{ color: 'var(--accent)' }}
                      onClick={() => void openExternalUrl(m.docs_url!)}
                    >
                      Setup docs…
                    </button>
                  )}
                  {(signalNeedsInstall || signalNeedsJava) && (
                    <div className="space-y-1">
                      {signalNeedsJava && (
                        <div
                          className="text-[10px] leading-snug"
                          style={{ color: 'var(--warning, #c9a227)' }}
                        >
                          Install Java 21+ (Temurin) and restart Remedy so Signal can run.
                        </div>
                      )}
                      {signalNeedsInstall && (
                        <button
                          type="button"
                          className="text-[10px] px-2 py-0.5 rounded"
                          style={{
                            background: 'var(--accent)',
                            color: 'var(--accent-fg, #fff)',
                            opacity: signalBusy ? 0.6 : 1,
                          }}
                          disabled={signalBusy}
                          onClick={() => void onInstallSignal()}
                        >
                          {signalBusy ? 'Installing…' : 'Install signal-cli'}
                        </button>
                      )}
                      {signalNeedsJava && !signalNeedsInstall && (
                        <button
                          type="button"
                          className="text-[10px] px-2 py-0.5 rounded"
                          style={{
                            background: 'var(--bg-secondary, var(--bg-tertiary))',
                            color: 'var(--text-secondary)',
                            border: '1px solid var(--border)',
                            opacity: signalBusy ? 0.6 : 1,
                          }}
                          disabled={signalBusy}
                          onClick={() => void onInstallSignal()}
                          title="Re-check or reinstall the managed signal-cli copy"
                        >
                          {signalBusy ? 'Checking…' : 'Re-check signal-cli'}
                        </button>
                      )}
                      {signalNeedsJava && (
                        <button
                          type="button"
                          className="block text-[9px] underline"
                          style={{ color: 'var(--accent)' }}
                          onClick={() => void openExternalUrl(SIGNAL_JAVA_DOWNLOAD_URL)}
                        >
                          Get Temurin Java 21…
                        </button>
                      )}
                      {signalNeedsInstall && typeof m.health?.download_url === 'string' && (
                        <button
                          type="button"
                          className="block text-[9px] underline"
                          style={{ color: 'var(--accent)' }}
                          onClick={() => void openExternalUrl(String(m.health!.download_url))}
                        >
                          Download page…
                        </button>
                      )}
                      {signalMsg && (
                        <div className="text-[9px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                          {signalMsg}
                        </div>
                      )}
                    </div>
                  )}
                  {(m.field_schema || []).length === 0 && (
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      No fields yet ({m.status}).
                    </div>
                  )}
                  {(m.field_schema || []).map((f) => {
                    if (f.kind === 'bool') {
                      return (
                        <label
                          key={f.key}
                          className="flex items-center gap-2 text-[11px] cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={Boolean(draft[f.key])}
                            onChange={(e) => patch(m.id, f.key, e.target.checked)}
                            style={{ accentColor: 'var(--accent)' }}
                          />
                          <span style={{ color: 'var(--text-secondary)' }}>{f.label}</span>
                        </label>
                      )
                    }
                    const isSecret = f.kind === 'secret'
                    const val = typeof draft[f.key] === 'string' ? (draft[f.key] as string) : ''
                    return (
                      <div key={f.key}>
                        <label
                          className="block text-[10px] mb-0.5"
                          style={{ color: 'var(--text-muted)' }}
                        >
                          {f.label}
                          {isSecret && m.token_set ? ' (configured)' : ''}
                        </label>
                        <input
                          type={isSecret ? 'password' : 'text'}
                          value={val}
                          placeholder={
                            isSecret && m.token_set
                              ? 'Leave blank to keep'
                              : f.placeholder || ''
                          }
                          onChange={(e) => patch(m.id, f.key, e.target.value)}
                          className="ui-input"
                          autoComplete="off"
                        />
                        {f.help && (
                          <div className="text-[9px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                            {f.help}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </SettingsSection>
  )
}
