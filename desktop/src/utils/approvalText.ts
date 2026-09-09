/** Plain-language copy for the approvals banner. */

import type { PendingApproval } from '../api/partner'

const ORIGIN_LABELS: Record<string, string> = {
  telegram: 'Telegram',
  discord: 'Discord',
  whatsapp: 'WhatsApp',
  signal: 'Signal',
  slack: 'Slack',
  sms: 'SMS',
  phone: 'a phone call',
  voice: 'voice',
  email: 'email',
  desktop: 'this desktop',
  webui: 'the web UI',
  api: 'the API',
  automation: 'an automation',
  cron: 'a scheduled task',
}

type OriginFields = Pick<PendingApproval, 'origin' | 'channel'>

/** Human label for the request origin, or null when the item carries none. */
export function approvalOriginLabel(item: OriginFields): string | null {
  const raw = String(item.origin || item.channel || '').trim()
  if (!raw) return null
  const key = raw.toLowerCase()
  if (ORIGIN_LABELS[key]) return ORIGIN_LABELS[key]
  return raw.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * Headline for the card: the plain-language summary (falling back to the
 * reason) plus "· via <origin>" when the request did not start here.
 */
export function approvalHeadline(
  item: Pick<PendingApproval, 'summary' | 'reason'> & OriginFields,
): string {
  const base = (item.summary || item.reason || '').trim()
  const origin = approvalOriginLabel(item)
  if (!origin) return base
  return base ? `${base} · via ${origin}` : `Request via ${origin}`
}

/**
 * Items that a "resolve several at once" affordance may ever include.
 * Sensitive checkpoints (payment / credentials / vault) are always excluded:
 * each one is answered on its own card, in every approval mode.
 */
export function bulkApprovable<T extends Pick<PendingApproval, 'sensitive'>>(items: T[]): T[] {
  return items.filter((i) => !i.sensitive)
}
