/**
 * Keep a long thinking scratchpad readable: same-round tokens concatenate
 * as the model sent them (no invented spaces), new rounds after a tool
 * start a fresh paragraph, and display splits those rounds into chunks
 * instead of one run-on wall.
 */

export const THINKING_ROUND_GAP = '\n\n'

/** Close the current thinking round so the next delta starts a new paragraph. */
export function closeThinkingRound(prev: string): string {
  const s = prev || ''
  if (!s.trim()) return s
  return /\n\n\s*$/.test(s) ? s.replace(/\s+$/, '') + THINKING_ROUND_GAP : s.replace(/\s+$/, '') + THINKING_ROUND_GAP
}

/**
 * Join a thinking delta onto the accumulated scratchpad.
 * - `append`: same HTTP round; concatenate verbatim (tokens already carry spaces).
 * - `replace-round`: a snapshot of the *current* round (or the first token of a
 *   new one if the previous round was already closed). Earlier rounds stay.
 */
export function joinThinkingDelta(
  prev: string,
  chunk: string,
  mode: 'append' | 'replace-round' = 'append',
): string {
  if (!chunk) return prev || ''
  if (!prev) return chunk
  if (mode === 'append') {
    if (/\n\n\s*$/.test(prev)) {
      return prev.replace(/\s+$/, '') + THINKING_ROUND_GAP + chunk.replace(/^\s+/, '')
    }
    return prev + chunk
  }
  const closed = /\n\n\s*$/.test(prev)
  const parts = splitThinkingChunks(prev)
  const tail = chunk.replace(/^\s+/, '')
  if (parts.length === 0) return chunk
  if (closed) return [...parts, tail].join(THINKING_ROUND_GAP)
  return [...parts.slice(0, -1), tail].join(THINKING_ROUND_GAP)
}

/** Split on blank lines; trim each round. */
export function splitThinkingChunks(text: string): string[] {
  return (text || '')
    .split(/\n{2,}/)
    .map((s) => s.replace(/[ \t]+$/gm, '').trim())
    .filter(Boolean)
}

/**
 * A long chunk with no newlines is a wall of sentences. Group them into
 * short paragraphs so a long run can be read. Leaves short or already
 * broken text alone — including mid-word token streams.
 */
export function paragraphizeThinking(chunk: string): string[] {
  const src = (chunk || '').trim()
  if (!src) return []
  if (/\n/.test(src) || src.length < 260) return [src]
  const sentences = src.match(/[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+$/g)
  if (!sentences || sentences.length < 3) return [src]
  const out: string[] = []
  let buf = ''
  for (const raw of sentences) {
    const piece = raw.trim()
    if (!piece) continue
    if (buf && buf.length + 1 + piece.length > 220) {
      out.push(buf)
      buf = piece
    } else {
      buf = buf ? `${buf} ${piece}` : piece
    }
  }
  if (buf) out.push(buf)
  return out.length ? out : [src]
}

/** Chunks to render in the thinking dropdown. */
export function thinkingDisplayChunks(text: string): string[] {
  return splitThinkingChunks(text).flatMap(paragraphizeThinking)
}
