/**
 * Small line-oriented unified diff for tool process (file writes/edits).
 */

export function splitLines(text: string): string[] {
  if (!text) return []
  // Keep empty trailing line only if present as content
  const normalized = text.replace(/\r\n/g, '\n')
  if (normalized === '') return []
  return normalized.split('\n')
}

/**
 * LCS-based line diff → unified-diff style lines (+ / - / space prefix).
 * Good enough for file_write previews (typically small).
 */
export function unifiedLineDiff(
  oldText: string,
  newText: string,
  opts?: { oldLabel?: string; newLabel?: string; context?: number },
): string {
  const a = splitLines(oldText)
  const b = splitLines(newText)
  const oldLabel = opts?.oldLabel || 'a'
  const newLabel = opts?.newLabel || 'b'
  const context = opts?.context ?? 2

  if (a.length === 0 && b.length === 0) {
    return `--- ${oldLabel}\n+++ ${newLabel}\n`
  }

  // LCS lengths
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i]![j] =
        a[i] === b[j] ? (dp[i + 1]![j + 1]! + 1) : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!)
    }
  }

  // Build raw ops
  type Op = { t: 'eq' | 'del' | 'add'; line: string }
  const ops: Op[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ t: 'eq', line: a[i]! })
      i++
      j++
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      ops.push({ t: 'del', line: a[i]! })
      i++
    } else {
      ops.push({ t: 'add', line: b[j]! })
      j++
    }
  }
  while (i < n) {
    ops.push({ t: 'del', line: a[i]! })
    i++
  }
  while (j < m) {
    ops.push({ t: 'add', line: b[j]! })
    j++
  }

  // Collapse to hunks with context
  const changed = ops.map((o) => o.t !== 'eq')
  const keep = new Array(ops.length).fill(false)
  for (let k = 0; k < ops.length; k++) {
    if (!changed[k]) continue
    for (let t = Math.max(0, k - context); t <= Math.min(ops.length - 1, k + context); t++) {
      keep[t] = true
    }
  }
  // If everything equal, show a short notice
  if (!keep.some(Boolean)) {
    return [
      `--- ${oldLabel}`,
      `+++ ${newLabel}`,
      '@@ (no line changes) @@',
      ...b.slice(0, 8).map((l) => ` ${l}`),
      b.length > 8 ? ` … (${b.length} lines total)` : '',
    ]
      .filter(Boolean)
      .join('\n')
  }

  const out: string[] = [`--- ${oldLabel}`, `+++ ${newLabel}`, '@@ edit @@']
  let gap = false
  for (let k = 0; k < ops.length; k++) {
    if (!keep[k]) {
      gap = true
      continue
    }
    if (gap) {
      out.push('@@ … @@')
      gap = false
    }
    const op = ops[k]!
    if (op.t === 'eq') out.push(` ${op.line}`)
    else if (op.t === 'del') out.push(`-${op.line}`)
    else out.push(`+${op.line}`)
  }
  return out.join('\n')
}

/** New file: every line is an addition. */
export function unifiedNewFile(path: string, content: string): string {
  const lines = splitLines(content)
  const label = path || 'new'
  const body = lines.length
    ? lines.map((l) => `+${l}`).join('\n')
    : '+\\ (empty file)'
  return [`--- /dev/null`, `+++ ${label}`, `@@ +${Math.max(lines.length, 1)} @@`, body].join(
    '\n',
  )
}
