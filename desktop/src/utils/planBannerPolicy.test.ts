import { describe, expect, it } from 'vitest'
import {
  isPlanActionable,
  isPlanTerminal,
  shouldShowPlanBanner,
  type TaskPlan,
} from '../api/plans'

function plan(status: string): TaskPlan {
  return { id: 'p1', title: 'T', status }
}

describe('plan banner policy', () => {
  it('treats done and cancelled as terminal', () => {
    expect(isPlanTerminal('done')).toBe(true)
    expect(isPlanTerminal('cancelled')).toBe(true)
    expect(isPlanTerminal('draft')).toBe(false)
    expect(isPlanTerminal('approved')).toBe(false)
    expect(isPlanTerminal('active')).toBe(false)
  })

  it('treats draft/approved/active as actionable', () => {
    expect(isPlanActionable('draft')).toBe(true)
    expect(isPlanActionable('approved')).toBe(true)
    expect(isPlanActionable('active')).toBe(true)
    expect(isPlanActionable('done')).toBe(false)
    expect(isPlanActionable('cancelled')).toBe(false)
  })

  it('always shows banner in plan mode (empty or with plan)', () => {
    expect(shouldShowPlanBanner(null, true)).toBe(true)
    expect(shouldShowPlanBanner(plan('done'), true)).toBe(true)
    expect(shouldShowPlanBanner(plan('draft'), true)).toBe(true)
  })

  it('does not stick terminal plans in build mode', () => {
    expect(shouldShowPlanBanner(plan('done'), false)).toBe(false)
    expect(shouldShowPlanBanner(plan('cancelled'), false)).toBe(false)
    expect(shouldShowPlanBanner(null, false)).toBe(false)
  })

  it('shows draft plans in build mode (still need Approve)', () => {
    expect(shouldShowPlanBanner(plan('draft'), false)).toBe(true)
  })

  it('hides approved/active plans in build mode (plan already in motion)', () => {
    expect(shouldShowPlanBanner(plan('approved'), false)).toBe(false)
    expect(shouldShowPlanBanner(plan('active'), false)).toBe(false)
  })

  it('still shows approved/active when back in plan mode (revise/cancel)', () => {
    expect(shouldShowPlanBanner(plan('approved'), true)).toBe(true)
    expect(shouldShowPlanBanner(plan('active'), true)).toBe(true)
  })
})
