/**
 * Chat feed windowing: mount only the newest `FEED_WINDOW` rows and reveal
 * earlier ones in chunks of the same size. Mounting a 2 000-row transcript at
 * once froze the webview; "show all" now walks back one chunk per click.
 */

export const FEED_WINDOW = 80

/** Index of the first row to mount given how many extra rows were revealed. */
export function feedWindowStart(total: number, revealed: number, window = FEED_WINDOW): number {
  const t = Math.max(0, Math.floor(total))
  const r = Math.max(0, Math.floor(revealed))
  return Math.max(0, t - window - r)
}

/** Rows still hidden above the mounted window. */
export function feedHiddenCount(total: number, revealed: number, window = FEED_WINDOW): number {
  return feedWindowStart(total, revealed, window)
}

/** How many rows the next "load earlier" click will add (never more than remain). */
export function feedNextChunk(total: number, revealed: number, window = FEED_WINDOW): number {
  return Math.min(window, feedHiddenCount(total, revealed, window))
}
