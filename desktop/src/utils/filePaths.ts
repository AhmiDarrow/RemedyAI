/** Join an API-relative file path without damaging Windows or POSIX absolute paths. */
export function absoluteFilePath(root: string, path: string): string {
  if (!root || /^[A-Za-z]:[\\/]/.test(path) || /^[\\/]/.test(path)) return path
  const separator = root.includes('\\') ? '\\' : '/'
  const base = root.replace(/[/\\]+$/, '')
  const tail = path.replace(/^\.([/\\]|$)/, '').replace(/[/\\]/g, separator)
  return tail ? `${base}${separator}${tail}` : root
}

export function parentFilePath(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '')
  if (!normalized || normalized === '.') return path.startsWith('/') ? '/' : '.'
  if (/^[A-Za-z]:$/.test(normalized)) return `${normalized}/`
  // A UNC server/share is a root, not a navigable parent directory.
  if (/^\/\/[^/]+\/[^/]+$/.test(normalized)) return normalized
  const index = normalized.lastIndexOf('/')
  if (index < 0) return '.'
  const parent = normalized.slice(0, index)
  if (/^[A-Za-z]:$/.test(parent)) return `${parent}/`
  return parent || '/'
}
