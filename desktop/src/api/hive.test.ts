import { describe, expect, it } from 'vitest'
import { hiveIsLive, hiveRowLabel, type HiveRosterRow } from './hive'

function row(partial: Partial<HiveRosterRow>): HiveRosterRow {
  return {
    id: 'abc',
    cadence: 'forager',
    status: 'reported',
    goal: 'review auth.py',
    done: true,
    outcome: 'ok',
    blockers: [],
    updated_at: '',
    pulse_s: 0,
    ...partial,
  }
}

describe('hive roster labels', () => {
  it('names cadence, status, and goal without a transcript', () => {
    expect(hiveRowLabel(row({}))).toBe('forager · reported · review auth.py')
  })

  it('treats retired and cancelled as not live', () => {
    expect(hiveIsLive(row({ status: 'asleep' }))).toBe(true)
    expect(hiveIsLive(row({ status: 'retired' }))).toBe(false)
    expect(hiveIsLive(row({ status: 'cancelled' }))).toBe(false)
  })
})
