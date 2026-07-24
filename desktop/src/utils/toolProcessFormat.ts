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
