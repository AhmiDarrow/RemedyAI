import { describe, expect, it } from 'vitest'
import { extractPlanOptions, stepStatusChip } from './PlanBanner'

describe('stepStatusChip', () => {
  it('maps backend step statuses to the four chip kinds', () => {
    expect(stepStatusChip('pending')).toEqual({ key: 'draft', label: 'draft' })
    expect(stepStatusChip(undefined)).toEqual({ key: 'draft', label: 'draft' })
    expect(stepStatusChip('active')).toEqual({ key: 'active', label: 'active' })
    expect(stepStatusChip('done')).toEqual({ key: 'done', label: 'done' })
    expect(stepStatusChip('skipped')).toEqual({ key: 'blocked', label: 'skipped' })
    expect(stepStatusChip('blocked')).toEqual({ key: 'blocked', label: 'blocked' })
  })
})

describe('extractPlanOptions', () => {
  it('finds Option A / Option B lines across goal, steps and risks', () => {
    const opts = extractPlanOptions({
      id: 'p1',
      title: 'Voice output',
      goal: 'Two ways forward:\nOption A: keep the worker\n- Option B — swap to native',
      steps: [
        { id: 's1', title: 'Decide', detail: '**Option C:** do nothing' },
        { id: 's2', title: 'Option A: already listed (dedupe)' },
      ],
      risks: ['option d - lowercase still counts'],
    })
    expect(opts).toEqual(['Option A', 'Option B', 'Option C', 'Option D'])
  })

  it('ignores prose mentions without a separator and empty plans', () => {
    expect(extractPlanOptions(null)).toEqual([])
    expect(
      extractPlanOptions({
        id: 'p2',
        title: 'No choices',
        goal: 'We could add an option a user toggles later.',
        steps: [{ id: 's', title: 'Do it' }],
      }),
    ).toEqual([])
  })
})
