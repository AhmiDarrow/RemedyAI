/** Build a short session title from the first user prompt. */

/** True when title is just a filesystem path / image dump (not a real name). */
export function looksLikePathTitle(title: string | null | undefined): boolean {
  const t = (title || '').trim()
  if (!t) return false
  // Windows / UNC / POSIX absolute paths, or bare image filenames used as titles
  if (/^[A-Za-z]:[\\/]/.test(t)) return true
  if (t.startsWith('\\\\') || t.startsWith('/Users/') || t.startsWith('/home/')) return true
  if (/\\/.test(t) && /\.(png|jpe?g|gif|webp|bmp|heic|pdf|docx?)$/i.test(t)) return true
  if (/^Screenshot\b/i.test(t) && /\.(png|jpe?g|gif|webp)$/i.test(t)) return true
  return false
}

/** Short label from an attachment path/name (basename, no extension). */
export function titleFromAttachmentName(name: string, maxLen = 52): string {
  let t = (name || '').trim().replace(/\//g, '\\')
  if (!t) return 'Attachment'
  const base = t.split(/[/\\]/).pop() || t
  // Drop extension for images
  const pretty = base.replace(/\.(png|jpe?g|gif|webp|bmp|heic)$/i, '')
  t = pretty.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (!t) t = 'Image'
  // Prefer "Screenshot" style over timestamp soup
  if (/^Screenshot\b/i.test(t)) {
    t = t.replace(/\s+\d{4}.*$/, '').trim() || 'Screenshot'
  }
  if (t.length > maxLen) t = `${t.slice(0, maxLen - 1).trimEnd()}…`
  return t
}

export function titleFromPrompt(text: string, maxLen = 52): string {
  let t = (text || '').trim().replace(/\s+/g, ' ')
  if (!t) return 'New Session'
  // Strip attachment appendix used in chat display
  const att = t.indexOf('📎')
  if (att >= 0) t = t.slice(0, att).trim() || t
  if (/^\(see attached/i.test(t)) return 'Attachments'
  // Don't put full OneDrive/Desktop paths in the tab bar
  if (looksLikePathTitle(t)) return titleFromAttachmentName(t, maxLen)
  if (t.length > maxLen) t = `${t.slice(0, maxLen - 1).trimEnd()}…`
  return t || 'New Session'
}

export function isPlaceholderTitle(title: string | null | undefined): boolean {
  const t = (title || '').trim().toLowerCase()
  if (!t || t === 'new session' || t === 'new chat' || t === 'untitled') return true
  // Path titles should be replaced when a real prompt arrives
  if (looksLikePathTitle(title)) return true
  if (t === 'attachments' || t === 'attachment' || t === 'image' || t === 'screenshot') {
    return true
  }
  return false
}
