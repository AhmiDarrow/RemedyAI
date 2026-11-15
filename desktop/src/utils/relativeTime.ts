/** Human-friendly relative time for sidebar / chat separators.
 *
 * The locale argument is optional. Callers that omit it keep the compact
 * English strings ("just now", "3m ago") so existing tests stay stable.
 * A passed locale follows the chosen UI language, including English.
 */

import { intlLocale } from '../i18n/locale'

function dateTag(locale?: string): string | undefined {
  if (locale === undefined) return undefined
  return intlLocale(locale)
}

function compactEnglish(diff: number, when: number, dateLocale: string | undefined): string {
  if (diff < 45) return 'just now'
  if (diff < 90) return '1m ago'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 5400) return '1h ago'
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 172800) return 'yesterday'
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`
  if (diff < 86400 * 30) return `${Math.floor(diff / (86400 * 7))}w ago`
  return new Date(when).toLocaleDateString(dateLocale, { month: 'short', day: 'numeric' })
}

function localizedAgo(diff: number, when: number, tag: string): string {
  const rtf = new Intl.RelativeTimeFormat(tag, { numeric: 'auto' })
  if (diff < 45) return rtf.format(0, 'second')
  if (diff < 3600) return rtf.format(-Math.max(1, Math.floor(diff / 60)), 'minute')
  if (diff < 86400) return rtf.format(-Math.max(1, Math.floor(diff / 3600)), 'hour')
  if (diff < 86400 * 7) return rtf.format(-Math.max(1, Math.floor(diff / 86400)), 'day')
  if (diff < 86400 * 30) return rtf.format(-Math.max(1, Math.floor(diff / (86400 * 7))), 'week')
  return new Date(when).toLocaleDateString(tag, { month: 'short', day: 'numeric' })
}

export function relativeTime(
  iso: string | null | undefined,
  now = Date.now(),
  locale?: string,
): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Math.round((now - t) / 1000)
  const tag = dateTag(locale)
  if (tag && tag !== 'en') {
    try {
      return localizedAgo(diff, t, tag)
    } catch {
      /* keep the English compact form */
    }
  }
  return compactEnglish(diff, t, tag)
}

export function dayKey(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

export function dayLabel(
  iso: string | null | undefined,
  now = Date.now(),
  locale?: string,
): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date(now)
  const yest = new Date(now - 86400000)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
  const tag = dateTag(locale)
  const dateOpts: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() !== today.getFullYear() ? 'numeric' : undefined,
  }
  if (!tag || tag === 'en') {
    if (sameDay(d, today)) return 'Today'
    if (sameDay(d, yest)) return 'Yesterday'
    return d.toLocaleDateString(tag, dateOpts)
  }
  try {
    const rtf = new Intl.RelativeTimeFormat(tag, { numeric: 'auto' })
    if (sameDay(d, today)) return rtf.format(0, 'day')
    if (sameDay(d, yest)) return rtf.format(-1, 'day')
    return d.toLocaleDateString(tag, dateOpts)
  } catch {
    if (sameDay(d, today)) return 'Today'
    if (sameDay(d, yest)) return 'Yesterday'
    return d.toLocaleDateString(undefined, dateOpts)
  }
}
