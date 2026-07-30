/**
 * react-markdown v9+ defaultUrlTransform only allows http(s)/mailto/irc/xmpp.
 * That strips every image src Remedy actually embeds:
 *   data:image/…  ·  C:\… paths  ·  file://  ·  blob:
 * Use this transform for chat bubbles so ChatImage receives a real src.
 */

/** Active content — never pass through. */
const BLOCKED = /^(javascript|vbscript):/i

/** Non-image data URIs (HTML, SVG scripts via text, etc.). */
const BLOCKED_DATA = /^data:(?!image\/)/i

/**
 * Allow chat image schemes and local filesystem paths; block active content.
 * Applied to both img src and a href — MessageFeed still only navigates
 * https/mailto on anchors.
 */
export function chatMarkdownUrlTransform(url: string): string {
  const v = (url || '').trim()
  if (!v) return ''

  if (BLOCKED.test(v) || BLOCKED_DATA.test(v)) return ''

  // http(s), data:image/*, blob:, file:
  if (/^(https?:|data:image\/|blob:|file:)/i.test(v)) return v

  // Windows drive path: C:\… or C:/…
  if (/^[A-Za-z]:[\\/]/.test(v)) return v

  // UNC
  if (v.startsWith('\\\\') || v.startsWith('//')) {
    // Protocol-relative //evil.com is not a local image — only UNC-style \\
    if (v.startsWith('//') && !v.startsWith('\\\\')) return ''
    return v
  }

  // Relative / project paths (assets/…, .remedy/…, /api/…) — no scheme
  // Reject unknown schemes like ftp: or custom: while allowing paths with colons later in the string
  const colon = v.indexOf(':')
  if (colon === -1) return v
  const slash = v.indexOf('/')
  const backslash = v.indexOf('\\')
  const firstSep =
    slash === -1
      ? backslash
      : backslash === -1
        ? slash
        : Math.min(slash, backslash)
  // Colon before any path separator ⇒ scheme (already handled allowlist above)
  if (firstSep === -1 || colon < firstSep) return ''

  return v
}
