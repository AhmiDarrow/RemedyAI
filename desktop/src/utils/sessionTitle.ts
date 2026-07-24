/** Build a short session title from the first user prompt. */

export function titleFromPrompt(text: string, maxLen = 52): string {
  let t = (text || '').trim().replace(/\s+/g, ' ')
  if (!t) return 'New Session'
  // Strip attachment appendix used in chat display
  const att = t.indexOf('📎')
  if (att >= 0) t = t.slice(0, att).trim() || t
  if (/^\(see attached/i.test(t)) return 'Attachments'
  if (t.length > maxLen) t = `${t.slice(0, maxLen - 1).trimEnd()}…`
  return t || 'New Session'
}

export function isPlaceholderTitle(title: string | null | undefined): boolean {
  const t = (title || '').trim().toLowerCase()
  return !t || t === 'new session' || t === 'new chat' || t === 'untitled'
}
