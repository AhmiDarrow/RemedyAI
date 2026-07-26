/**
 * Promote bare filesystem image paths in chat text to markdown images so
 * ChatImage / lightbox can render them (models often paste paths, not ![]()).
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
    // ~/.remedy or relative assets
    String.raw`(?:~[\\/]|\.\/)?(?:\.remedy[\\/]|assets[\\/])[^\n<>"'|]*?\.${EXT}` +
    String.raw`)`,
  'gi',
)

function alreadyMarkdownImage(text: string, matchStart: number): boolean {
  const before = text.slice(Math.max(0, matchStart - 4), matchStart)
  return /!\[/.test(before) || before.endsWith('](')
}

/**
 * Rewrite bare image paths to `![](path)` once per unique path.
 * Leaves existing markdown images alone.
 */
export function linkifyBareImagePaths(text: string): string {
  if (!text) return text
  return text.replace(PATH_RE, (raw, _g, offset: number) => {
    const path = String(raw).trim()
    if (!path) return raw
    if (alreadyMarkdownImage(text, offset)) return raw
    // Avoid double-wrapping if already in markdown
    if (text.slice(Math.max(0, offset - 2), offset) === '](') return raw
    const safe = path.replace(/\\/g, '/')
    const name = path.split(/[/\\]/).pop() || 'image'
    return `![${name}](${safe})`
  })
}
