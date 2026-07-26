/**
 * Detect unified diffs and classify lines for red/green rendering.
 */

export type DiffLineKind = 'add' | 'del' | 'hunk' | 'meta' | 'ctx'

export function looksLikeUnifiedDiff(text: string): boolean {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  if (lines.length < 2) return false
  let plus = 0
  let minus = 0
  let hunk = 0
  let headers = 0
  for (const line of lines) {
    if (line.startsWith('@@')) hunk++
    else if (
      line.startsWith('diff --git')
      || line.startsWith('index ')
      || line.startsWith('+++ ')
      || line.startsWith('--- ')
    ) {
      headers++
    } else if (line.startsWith('+') && !line.startsWith('+++')) plus++
    else if (line.startsWith('-') && !line.startsWith('---')) minus++
  }
  if (hunk > 0) return true
  if (headers >= 2) return true
  // Enough +/- lines to not be a random list
  if (plus >= 1 && minus >= 1 && plus + minus >= 3) return true
  return false
}

export function isDiffLanguage(className?: string | null): boolean {
  if (!className) return false
  return /language-(diff|patch|udiff)/i.test(className)
}

export function classifyDiffLine(line: string): DiffLineKind {
  if (
    line.startsWith('diff --git')
    || line.startsWith('index ')
    || line.startsWith('+++ ')
    || line.startsWith('--- ')
    || line.startsWith('new file')
    || line.startsWith('deleted file')
    || line.startsWith('similarity index')
    || line.startsWith('rename from')
    || line.startsWith('rename to')
    || line.startsWith('Binary files')
  ) {
    return 'meta'
  }
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return 'ctx'
}

/** Whether this code fence should use line-colored diff rendering. */
export function shouldRenderAsDiff(text: string, className?: string | null): boolean {
  if (isDiffLanguage(className)) return true
  return looksLikeUnifiedDiff(text)
}
