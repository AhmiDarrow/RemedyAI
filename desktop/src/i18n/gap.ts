/**
 * Desktop-only chrome that the server catalog does not carry.
 * Merged after the server catalog so a missing server key stays English
 * and these keys still translate. Traditional Chinese is not a target:
 * gapFor('zh-Hant') is empty so those users keep English for these keys.
 */
import { de } from './gap/de'
import { es } from './gap/es'
import { fr } from './gap/fr'
import { it } from './gap/it'
import { ja } from './gap/ja'
import { ko } from './gap/ko'
import { pl } from './gap/pl'
import { pt } from './gap/pt'
import { ru } from './gap/ru'
import { zhHans } from './gap/zhHans'

export const GAP: Record<string, Record<string, string>> = {
  es,
  fr,
  de,
  pt,
  it,
  ru,
  pl,
  ja,
  ko,
  'zh-Hans': zhHans,
}

const SUPPORTED = new Set(Object.keys(GAP))

/** Map a resolved UI language id onto a gap catalog id, or '' for none. */
export function gapLocaleId(resolved: string | null | undefined): string {
  const low = (resolved || '').trim().toLowerCase().replace(/_/g, '-')
  if (!low || low === 'auto' || low === 'en' || low.startsWith('en-')) return ''
  if (low === 'zh-hant' || low === 'zh-tw' || low === 'zh-hk' || low === 'zh-mo') return ''
  if (low === 'zh' || low === 'zh-cn' || low === 'zh-sg' || low === 'zh-hans') return 'zh-Hans'
  if (low === 'pt' || low.startsWith('pt-')) return 'pt'
  const primary = low.split('-')[0] || ''
  return SUPPORTED.has(primary) ? primary : ''
}

export function gapFor(resolved: string | null | undefined): Record<string, string> {
  const id = gapLocaleId(resolved)
  if (!id) return {}
  return GAP[id] || {}
}
