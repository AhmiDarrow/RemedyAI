/**
 * Promote bare filesystem image paths in chat text to markdown images so
 * ChatImage / lightbox can render them (models often paste paths, not ![]()).
 * Also unwraps backtick-wrapped image paths from older attachment blocks.
 */

const EXT = String.raw`(?:png|jpe?g|gif|webp|bmp|ico|svg)`

/** Windows absolute, UNC, file://, and common relative project paths. */
const PATH_RE = new RegExp(
  String.raw`(?<!\]\()(?<!\]:\s)(` +
    // file:// URL
    String.raw`file:\/\/\/[^\s<>"'|]+?\.${EXT}` +
    String.raw`|` +
    // Windows drive path (allow spaces in path segments)
    String.raw`[A-Za-z]:[\\/][^\n<>"'|]*?\.${EXT}` +
    String.raw`|` +
    // UNC
    String.raw`\\\\[^\n<>"'|]+?\.${EXT}` +
    String.raw`|` +
    // ~/.remedy or relative assets (attachments live under .remedy/)
    String.raw`(?:~[\\/]|\.\/)?(?:\.remedy[\\/]|assets[\\/]|attachments[\\/])[^\n<>"'|]*?\.${EXT}` +
    String.raw`)`,
  'gi',
)

/** `` `C:\…\shot.png` `` from older attachment lists */
const BACKTICK_PATH_RE = new RegExp(
  '`((?:file:\\/\\/\\/|[A-Za-z]:[\\\\/]|\\\\\\\\|~[\\\\/]|\\.?[\\\\/])[^`\\n]+?\\.' +
    EXT +
    ')`',
  'gi',
)

function alreadyMarkdownImage(text: string, matchStart: number): boolean {
  const before = text.slice(Math.max(0, matchStart - 4), matchStart)
  return /!\[/.test(before) || before.endsWith('](') || before.endsWith('](<')
}

function toMarkdownImage(path: string): string {
  const safe = path.replace(/\\/g, '/')
  const name = path.split(/[/\\]/).pop() || 'image'
  if (/[\s()]/.test(safe)) return `![${name}](<${safe}>)`
  return `![${name}](${safe})`
}

/**
 * Rewrite bare image paths to `![](path)` once per unique path.
 * Leaves existing markdown images alone.
 */
export function linkifyBareImagePaths(text: string): string {
  if (!text) return text
  // 1) Backtick-wrapped paths from stored attachment blocks
  let out = text.replace(BACKTICK_PATH_RE, (full, inner: string) => {
    const path = String(inner || '').trim()
    if (!path) return full
    return toMarkdownImage(path)
  })
  // 2) Bare paths (model-pasted, etc.)
  out = out.replace(PATH_RE, (raw, _g, offset: number) => {
    const path = String(raw).trim()
    if (!path) return raw
    if (alreadyMarkdownImage(out, offset)) return raw
    if (out.slice(Math.max(0, offset - 2), offset) === '](') return raw
    if (out.slice(Math.max(0, offset - 3), offset) === '](<') return raw
    return toMarkdownImage(path)
  })
  return out
}
