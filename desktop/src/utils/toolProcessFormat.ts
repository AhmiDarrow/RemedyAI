/**
 * Format tool args/results for ProcessTrace display.
 * file_write → synthetic unified diff (green adds / red deletes).
 */

import { unifiedLineDiff, unifiedNewFile } from './lineDiff'
import { shouldRenderAsDiff } from './diffHighlight'

export type FormattedToolBody = {
  /** Text for DiffCode / pre */
  text: string
  /** Force language-diff when we synthesized a patch */
  className?: string
  /** Small header above the body */
  caption?: string
}

function tryParseJson(raw: string | undefined): Record<string, unknown> | null {
  if (!raw || !raw.trim()) return null
  try {
    const v = JSON.parse(raw) as unknown
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      return v as Record<string, unknown>
    }
  } catch {
    /* not JSON */
  }
  return null
}

function strField(obj: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'string') return v
    if (v != null && typeof v !== 'object') return String(v)
  }
  return ''
}

/**
 * @param priorContentByPath — path → last content from earlier steps in this turn
 */
export function formatToolArgsDisplay(
  toolName: string,
  argsText: string | undefined,
  priorContentByPath: Map<string, string>,
): FormattedToolBody {
  const raw = argsText || ''
  const name = (toolName || '').toLowerCase()
  const obj = tryParseJson(raw)

  if (
    obj
    && (name === 'file_write' || name === 'write_file' || name === 'create_file')
  ) {
    const path = strField(obj, 'path', 'file', 'filepath', 'file_path')
    const content = strField(obj, 'content', 'text', 'body', 'data')
    const prior = path ? priorContentByPath.get(path) : undefined

    if (path && content !== undefined) {
      // Remember for subsequent writes in this process list
      priorContentByPath.set(path, content)

      if (prior === undefined || prior === '') {
        return {
          text: unifiedNewFile(path, content),
          className: 'language-diff',
          caption: `New file · ${path}`,
        }
      }
      if (prior === content) {
        return {
          text: unifiedLineDiff(prior, content, {
            oldLabel: path,
            newLabel: path,
          }),
          className: 'language-diff',
          caption: `Unchanged · ${path}`,
        }
      }
      return {
        text: unifiedLineDiff(prior, content, {
          oldLabel: `${path} (before)`,
          newLabel: `${path} (after)`,
        }),
        className: 'language-diff',
        caption: `Edit · ${path}`,
      }
    }
  }

  // Already a unified diff (agent put patch in args)
  if (shouldRenderAsDiff(raw)) {
    return { text: raw, className: 'language-diff' }
  }

  return { text: raw }
}

export function formatToolResultDisplay(
  _toolName: string,
  resultText: string | undefined,
): FormattedToolBody {
  const raw = resultText || ''
  if (shouldRenderAsDiff(raw)) {
    return { text: raw, className: 'language-diff' }
  }
  // file_write success often returns a short status string — leave as plain
  return { text: raw }
}

function oneLine(text: string, max = 140): string {
  const s = text.replace(/\s+/g, ' ').trim()
  if (!s) return ''
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

/**
 * Always-visible one-liner under a process step (Med depth).
 * Prefer path / command / query from args; fall back to result / error.
 */
export function stepInlineSummary(
  toolName: string,
  argsText?: string,
  resultText?: string,
  error?: string,
): string | null {
  if (error) return oneLine(`Error: ${error}`, 160)
  const name = (toolName || '').toLowerCase()
  const obj = tryParseJson(argsText)

  if (obj) {
    const path = strField(obj, 'path', 'file', 'filepath', 'file_path', 'directory', 'dir')
    const cmd = strField(obj, 'command', 'cmd', 'shell', 'code')
    const query = strField(obj, 'query', 'q', 'pattern', 'search', 'url', 'prompt', 'title')
    if (path) return oneLine(path, 160)
    if (cmd) return oneLine(cmd, 160)
    if (query) return oneLine(query, 160)
  }

  if (argsText && argsText.trim() && !argsText.trim().startsWith('{')) {
    return oneLine(argsText, 160)
  }

  if (resultText && resultText.trim()) {
    // Prefer first non-empty line
    const first =
      resultText
        .split(/\r?\n/)
        .map((l) => l.trim())
        .find((l) => l.length > 0) || resultText
    return oneLine(first, 160)
  }

  // JSON args with no known keys — show compact keys
  if (obj) {
    const keys = Object.keys(obj).slice(0, 4).join(', ')
    if (keys) return oneLine(`{${keys}}`, 120)
  }

  void name
  return null
}

/** Short multi-line body for Med (not a full dump). */
export function stepMediumPreview(
  resultText?: string,
  error?: string,
  opts?: { maxLines?: number; maxChars?: number },
): string {
  const maxLines = opts?.maxLines ?? 4
  const maxChars = opts?.maxChars ?? 480
  const raw = (error || resultText || '').trim()
  if (!raw) return ''
  const lines = raw.split(/\r?\n/)
  let out = lines.slice(0, maxLines).join('\n')
  if (lines.length > maxLines) out += '\n…'
  if (out.length > maxChars) out = `${out.slice(0, maxChars - 1)}…`
  return out
}
