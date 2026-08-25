import { apiFetch } from './client'

export interface UiLanguageOption {
  id: string
  name_en: string
  name_native: string
  rtl: boolean
  chrome: boolean
}

export interface I18nPayload {
  ui_language: string
  resolved: string
  dir: 'ltr' | 'rtl'
  chrome: boolean
  catalog: Record<string, string>
  languages: UiLanguageOption[]
}

export async function fetchI18n(lang?: string, hint?: string): Promise<I18nPayload> {
  const q = new URLSearchParams()
  if (lang) q.set('lang', lang)
  if (hint) q.set('hint', hint)
  const s = q.toString()
  return apiFetch<I18nPayload>(`/i18n${s ? `?${s}` : ''}`)
}
