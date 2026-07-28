import { describe, expect, it } from 'vitest'
import {
  CONFIRM_AT,
  SOFT_BADGE_AT,
  shouldConfirmNewTurn,
  shouldShowBusyBadge,
} from './concurrentTurns'

describe('concurrentTurns', () => {
  it('soft badge at 2+', () => {
    expect(shouldShowBusyBadge(SOFT_BADGE_AT - 1)).toBe(false)
    expect(shouldShowBusyBadge(SOFT_BADGE_AT)).toBe(true)
  })

  it('confirm at 3+', () => {
    expect(shouldConfirmNewTurn(CONFIRM_AT - 1)).toBe(false)
    expect(shouldConfirmNewTurn(CONFIRM_AT)).toBe(true)
  })
})
