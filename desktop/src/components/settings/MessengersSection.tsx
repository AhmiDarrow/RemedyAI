/** Settings → Messengers — nested expandables (one row per platform). */

import { useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
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
  /** Which messenger rows are expanded (collapsed by default). */
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

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
                    {m.status}
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
