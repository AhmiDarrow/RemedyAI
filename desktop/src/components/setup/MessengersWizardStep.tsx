/** Optional first-run wizard step for messenger connectors. */

import type { ReactNode } from 'react'
import type { MessengerInfo } from '../../api/settings'

export interface MessengersWizardStepProps {
  messengers: MessengerInfo[]
  /** id → enabled */
  enabled: Record<string, boolean>
  onToggle: (id: string, on: boolean) => void
  /** id → token draft */
  tokens: Record<string, string>
  onTokenChange: (id: string, token: string) => void
}

export function MessengersWizardStep({
  messengers,
  enabled,
  onToggle,
  tokens,
  onTokenChange,
}: MessengersWizardStepProps): ReactNode {
  const list =
    messengers.length > 0
      ? messengers
      : ([
          { id: 'telegram', name: 'Telegram', status: 'ready', enabled: false, token_set: false },
          { id: 'discord', name: 'Discord', status: 'partial', enabled: false, token_set: false },
          { id: 'slack', name: 'Slack', status: 'partial', enabled: false, token_set: false },
          { id: 'mattermost', name: 'Mattermost', status: 'partial', enabled: false, token_set: false },
        ] as MessengerInfo[])

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
          Messengers (optional)
        </h2>
        <p className="text-xs mt-1 leading-snug" style={{ color: 'var(--text-muted)' }}>
          Talk to Remedy from Telegram, Discord, and more. Chats show up in the session list
          so you can continue in the desktop app. Skip if you only want the desktop for now.
        </p>
      </div>
      <div className="space-y-2 max-h-[42vh] overflow-y-auto">
        {list.map((m) => {
          const on = Boolean(enabled[m.id])
          return (
            <div
              key={m.id}
              className="rounded p-2 space-y-1"
              style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
            >
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => onToggle(m.id, e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                  {m.name}
                </span>
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {m.status}
                </span>
              </label>
              {on && (
                <input
                  type="password"
                  value={tokens[m.id] || ''}
                  onChange={(e) => onTokenChange(m.id, e.target.value)}
                  placeholder="Bot token (optional now)"
                  className="w-full rounded px-2 py-1 text-xs outline-none"
                  style={{
                    background: 'var(--bg-secondary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border)',
                  }}
                  autoComplete="off"
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
