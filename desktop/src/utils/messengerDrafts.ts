/** Helpers for messenger settings drafts (keeps SettingsPanel thin). */

import type { MessengerInfo, SettingsUpdate } from '../api/settings'

export type MessengerDraftMap = Record<string, Record<string, string | boolean>>

export function draftsFromMessengers(list: MessengerInfo[]): MessengerDraftMap {
  const drafts: MessengerDraftMap = {}
  for (const m of list) {
    const d: Record<string, string | boolean> = { enabled: Boolean(m.enabled) }
    const fields = m.fields || {}
    for (const [k, v] of Object.entries(fields)) {
      if (Array.isArray(v)) d[k] = v.join(', ')
      else if (typeof v === 'boolean') d[k] = v
      else d[k] = v == null ? '' : String(v)
    }
    drafts[m.id] = d
  }
  return drafts
}

export function messengersUpdateFromDrafts(
  messengers: MessengerInfo[],
  messengerDrafts: MessengerDraftMap,
): SettingsUpdate['messengers'] | undefined {
  if (messengers.length === 0 && Object.keys(messengerDrafts).length === 0) {
    return undefined
  }
  const body: Record<string, Record<string, unknown>> = {}
  const ids = new Set([
    ...messengers.map((m) => m.id),
    ...Object.keys(messengerDrafts),
  ])
  for (const id of ids) {
    const draft = messengerDrafts[id] || {}
    const prev = messengers.find((m) => m.id === id)
    const entry: Record<string, unknown> = {
      enabled:
        draft.enabled !== undefined
          ? Boolean(draft.enabled)
          : Boolean(prev?.enabled),
    }
    for (const [k, v] of Object.entries(draft)) {
      if (k === 'enabled') continue
      const schema = prev?.field_schema?.find((f) => f.key === k)
      if (schema?.kind === 'secret') {
        if (typeof v === 'string' && v.trim()) entry[k] = v.trim()
        continue
      }
      entry[k] = v
    }
    body[id] = entry
  }
  return body
}

export function messengerBadgeLabel(originChannel?: string | null): string | null {
  if (!originChannel) return null
  const map: Record<string, string> = {
    telegram: 'Telegram',
    discord: 'Discord',
    slack: 'Slack',
    mattermost: 'Mattermost',
    whatsapp: 'WhatsApp',
    teams: 'Teams',
    matrix: 'Matrix',
    google_chat: 'GChat',
    signal: 'Signal',
  }
  return map[originChannel] || originChannel
}
