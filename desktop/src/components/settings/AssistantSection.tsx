/** Settings → Personal assistant — accounts (planned), brief prefs, money disclaimer. */

import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { SettingsSection } from '../SettingsSection'

export type AssistantDraft = {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  brief?: {
    enabled?: boolean
    hour_local?: number
    quiet_start?: number
    quiet_end?: number
    include_calendar?: boolean
    include_mail?: boolean
    include_goals?: boolean
    include_budget?: boolean
    messenger_delivery?: boolean
  }
}

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

export type AssistantStatus = {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  money_disclaimer?: string
  brief?: AssistantDraft['brief']
  accounts?: Array<{
    id?: string
    provider?: string
    email?: string
    status?: string
    capabilities?: string[]
  }>
  has_budget?: boolean
  debt_count?: number
  bill_count?: number
  providers_planned?: Array<{ id: string; name: string; status: string }>
}

export interface AssistantSectionProps {
  sectionProps: SectionProps
  assistant: AssistantStatus | null | undefined
  draft: AssistantDraft
  setDraft: Dispatch<SetStateAction<AssistantDraft>>
}

export function AssistantSection({
  sectionProps,
  assistant,
  draft,
  setDraft,
}: AssistantSectionProps): ReactNode {
  const brief = { ...(assistant?.brief || {}), ...(draft.brief || {}) }
  const enabled =
    draft.enabled !== undefined ? draft.enabled : Boolean(assistant?.enabled ?? true)
  const disclaimerAccepted =
    draft.money_disclaimer_accepted !== undefined
      ? draft.money_disclaimer_accepted
      : Boolean(assistant?.money_disclaimer_accepted)

  const patchBrief = (key: string, value: boolean | number) => {
    setDraft((prev) => ({
      ...prev,
      brief: { ...(prev.brief || {}), [key]: value },
    }))
  }

  const providers = assistant?.providers_planned || [
    { id: 'google', name: 'Google (Gmail + Calendar)', status: 'planned' },
    { id: 'microsoft', name: 'Microsoft (Outlook/Hotmail)', status: 'planned' },
    { id: 'yahoo', name: 'Yahoo Mail', status: 'planned' },
  ]

  return (
    <SettingsSection
      {...sectionProps}
      summary={
        enabled
          ? `On · budget ${assistant?.has_budget ? 'set' : '—'} · ${assistant?.debt_count ?? 0} debts · accounts soon`
          : 'Off'
      }
    >
      <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
        Additive helper for calendar, mail, budget organization, and briefings — not a new product
        mission. Official Google/Microsoft sign-in for mail/calendar comes next; budget tools work
        locally today via chat tools.
      </div>

      <label className="flex items-center gap-2 mb-2 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setDraft((p) => ({ ...p, enabled: e.target.checked }))}
          style={{ accentColor: 'var(--accent)' }}
        />
        <span style={{ color: 'var(--text-primary)' }}>Personal assistant features enabled</span>
      </label>

      <div className="mb-2">
        <div className="text-[10px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
          Linked accounts (OAuth — planned)
        </div>
        <ul className="text-[10px] space-y-0.5 m-0 pl-3" style={{ color: 'var(--text-muted)' }}>
          {providers.map((p) => (
            <li key={p.id}>
              {p.name} — <em>{p.status}</em>
            </li>
          ))}
        </ul>
        {(assistant?.accounts || []).length > 0 && (
          <ul className="text-[10px] mt-1 m-0 pl-3" style={{ color: 'var(--text-primary)' }}>
            {assistant!.accounts!.map((a) => (
              <li key={a.id || a.provider}>
                {a.provider}: {a.status} {a.email || ''}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mb-2">
        <div className="text-[10px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
          Daily brief (opt-in)
        </div>
        <label className="flex items-center gap-2 mb-1 cursor-pointer">
          <input
            type="checkbox"
            checked={Boolean(brief.enabled)}
            onChange={(e) => patchBrief('enabled', e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>Enable scheduled brief</span>
        </label>
        <label className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          Hour (local)
          <input
            type="number"
            min={0}
            max={23}
            className="w-12 rounded border px-1 py-0.5 text-[10px]"
            style={{
              borderColor: 'var(--border)',
              background: 'var(--bg-input, var(--bg-elevated))',
              color: 'var(--text-primary)',
            }}
            value={Number(brief.hour_local ?? 7)}
            onChange={(e) => patchBrief('hour_local', Number(e.target.value))}
          />
        </label>
        <div className="flex flex-wrap gap-2 mt-1 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          {(
            [
              ['include_calendar', 'Calendar'],
              ['include_mail', 'Mail'],
              ['include_goals', 'Goals'],
              ['include_budget', 'Budget'],
              ['messenger_delivery', 'Messenger delivery'],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={Boolean(brief[key])}
                onChange={(e) => patchBrief(key, e.target.checked)}
                style={{ accentColor: 'var(--accent)' }}
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div
        className="rounded border p-2 text-[10px] leading-snug mb-1"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <strong style={{ color: 'var(--text-primary)' }}>Money tools</strong>
        <p className="m-0 mt-1">
          {assistant?.money_disclaimer ||
            'Budgeting and debt tracking help you organize numbers you enter — not personalized financial, tax, or legal advice.'}
        </p>
        <label className="flex items-center gap-2 mt-2 cursor-pointer">
          <input
            type="checkbox"
            checked={disclaimerAccepted}
            onChange={(e) =>
              setDraft((p) => ({ ...p, money_disclaimer_accepted: e.target.checked }))
            }
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>I understand — organizational tools only</span>
        </label>
        <div className="mt-1" style={{ color: 'var(--text-muted)' }}>
          Local data: budget {assistant?.has_budget ? 'yes' : 'no'} · debts{' '}
          {assistant?.debt_count ?? 0} · bills {assistant?.bill_count ?? 0}. Chat tools:{' '}
          <code>budget_set</code>, <code>debt_upsert</code>, <code>assistant_brief</code>, …
        </div>
      </div>
    </SettingsSection>
  )
}
