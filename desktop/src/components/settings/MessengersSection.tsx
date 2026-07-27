/** Settings → Messengers — catalog list, enable, credentials, fields. */

import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { openExternalUrl } from '../../api/auth'
import type { MessengerInfo } from '../../api/settings'
import { SettingsSection } from '../SettingsSection'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'

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
}

const STATUS_COLOR: Record<string, string> = {
  ready: 'var(--success)',
  partial: 'var(--warning, #c9a227)',
  planned: 'var(--text-muted)',
}

export function MessengersSection({
  sectionProps,
  messengers,
  messengerDrafts,
  setMessengerDrafts,
}: MessengersSectionProps): ReactNode {
  const patch = (id: string, key: string, value: string | boolean) => {
    setMessengerDrafts((prev) => ({
      ...prev,
      [id]: { ...(prev[id] || {}), [key]: value },
    }))
  }

  return (
    <SettingsSection {...sectionProps}>
      <div className="text-[10px] leading-snug mb-2 space-y-1" style={{ color: 'var(--text-muted)' }}>
        <p style={{ margin: 0 }}>
          Connect Telegram, Discord, Mattermost, WhatsApp, and more.
          Messenger chats appear in the <strong style={{ color: 'var(--text-secondary)' }}>session list</strong>
          {' '}so you can continue in the app — updates sync in realtime.
        </p>
        <p style={{ margin: 0 }}>
          Tokens never leave the secure store. Empty secret fields leave the current token unchanged.
        </p>
      </div>

      {messengers.length === 0 && (
        <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          Catalog unavailable — save Settings once the server is up, or check Help → CLI &amp; API.
        </div>
      )}

      <div className="space-y-2">
        {messengers.map((m) => {
          const draft = messengerDrafts[m.id] || { enabled: m.enabled }
          const enabled = Boolean(draft.enabled)
          const statusColor = STATUS_COLOR[m.status] || 'var(--text-muted)'
          return (
            <div
              key={m.id}
              className="rounded p-2 space-y-1.5"
              style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
            >
              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-2 cursor-pointer min-w-0">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => patch(m.id, 'enabled', e.target.checked)}
                    style={{ accentColor: 'var(--accent)' }}
                  />
                  <span className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                    {m.name}
                  </span>
                </label>
                <span className="text-[10px] flex-shrink-0" style={{ color: statusColor }}>
                  {m.status}
                  {m.token_set ? ' · key set' : ''}
                </span>
              </div>
              {m.description && (
                <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                  {m.description}
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
              {enabled && (m.field_schema || []).length > 0 && (
                <div className="space-y-1.5 pt-1">
                  {(m.field_schema || []).map((f) => {
                    if (f.kind === 'bool') {
                      return (
                        <label key={f.key} className="flex items-center gap-2 text-[11px] cursor-pointer">
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
                        <label className="block text-[10px] mb-0.5" style={{ color: 'var(--text-muted)' }}>
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
                          className="w-full rounded px-2 py-1 text-xs outline-none"
                          style={{
                            background: 'var(--bg-secondary)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border)',
                          }}
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
