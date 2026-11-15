/** Map a stored Remedy language id to an Intl / BCP-47 tag. */

export function intlLocale(code: string | null | undefined): string | undefined {
  const raw = (code || '').trim()
  if (!raw) return undefined
  const id = raw.replace(/_/g, '-').toLowerCase()
  if (id === 'en' || id.startsWith('en-')) return 'en'
  if (id === 'zh-hans' || id === 'zh-cn' || id === 'zh-sg' || id === 'zh') return 'zh-CN'
  if (id === 'zh-hant' || id === 'zh-tw' || id === 'zh-hk' || id === 'zh-mo') return 'zh-TW'
  if (id === 'pt' || id === 'pt-br') return 'pt-BR'
  if (id === 'pt-pt') return 'pt-PT'
  const primary = id.split('-')[0]
  return primary || undefined
}

/** Group digits for the chosen language. An empty code follows the runtime default. */
export function formatCount(n: number, code?: string | null): string {
  const tag = code === undefined ? undefined : intlLocale(code)
  try {
    return n.toLocaleString(tag)
  } catch {
    return String(n)
  }
}
