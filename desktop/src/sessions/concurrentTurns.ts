/** Policy helpers for concurrent background turns (Phase A). */

/** Show sidebar "N running" emphasis starting at this many live turns. */
export const SOFT_BADGE_AT = 2

/** Confirm before starting another turn when this many are already running. */
export const CONFIRM_AT = 3

export function shouldShowBusyBadge(runningCount: number): boolean {
  return runningCount >= SOFT_BADGE_AT
}

export function shouldConfirmNewTurn(runningCount: number): boolean {
  return runningCount >= CONFIRM_AT
}

export function concurrentTurnConfirmMessage(runningCount: number): string {
  return (
    `${runningCount} sessions already have a live turn. `
    + 'Starting another can burn rate limits and contend for tools. Continue?'
  )
}
