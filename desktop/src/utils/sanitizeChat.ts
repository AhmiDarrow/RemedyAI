/** Strip model tool-markup dumps (DSML / XML-ish) so they never stay in the bubble. */

const DSML_RE =
  /(?:[|｜]{1,2}\s*DSML\s*[|｜]{1,2})|(?:tool[_\s-]?calls)|(?:function[_\s-]?calls)|<\/?invoke\b|<\/?parameter\b|invoke_parameter|name\s*=\s*["'](?:file_read|bash_exec|comfyui|list_dir|local_discover)["']/i

export function looksLikeToolMarkup(text: string): boolean {
  if (!text) return false
  return DSML_RE.test(text)
}

export function stripToolMarkup(text: string): string {
  if (!text) return ''
  let t = text
  t = t.replace(/[|｜]{1,2}\s*DSML\s*[|｜]{1,2}/gi, ' ')
  t = t.replace(/(?:tool[_\s-]?calls|function[_\s-]?calls)\b[\s\S]*?(?=\n{2,}|$)/gi, ' ')
  t = t.replace(/<\/?(?:invoke|parameter|invoke_parameter|invoke_step)[^>]*>/gi, ' ')
  t = t.replace(
    /name\s*=\s*["'](?:file_read|file_write|list_dir|bash_exec|comfyui|local_discover)["'][^<\n]*/gi,
    ' ',
  )
  t = t.replace(/[ \t]{2,}/g, ' ')
  t = t.replace(/\n{3,}/g, '\n\n')
  return t.trim()
}

/** Sanitize assistant text for display; empty if only tool spam. */
export function sanitizeAssistantText(text: string): string {
  if (!text) return ''
  if (!looksLikeToolMarkup(text)) return text
  return stripToolMarkup(text)
}
