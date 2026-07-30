import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sessions', () => ({
  abortSession: vi.fn(async () => ({ status: 'aborted' })),
}))

import {
  completeStreamJob,
  countRunningJobs,
  detachStreamJob,
  getBusySessionIds,
  registerStreamJob,
  stopStreamJob,
} from './streamJobs'

describe('streamJobs', () => {
  afterEach(() => {
    // Clear registry by completing any leftovers
    for (const id of getBusySessionIds()) {
      completeStreamJob(id, 'aborted')
    }
  })

  it('register and count running', () => {
    const c = new AbortController()
    registerStreamJob('s1', c, 'grok')
    expect(countRunningJobs()).toBe(1)
    expect(getBusySessionIds().has('s1')).toBe(true)
    completeStreamJob('s1', 'done')
    expect(countRunningJobs()).toBe(0)
  })

  it('detach leaves job running', () => {
    const c = new AbortController()
    registerStreamJob('s2', c)
    detachStreamJob('s2')
    expect(countRunningJobs()).toBe(1)
    completeStreamJob('s2', 'done')
  })

  it('stopStreamJob aborts', async () => {
    const c = new AbortController()
    registerStreamJob('s3', c)
    await stopStreamJob('s3')
    expect(c.signal.aborted).toBe(true)
  })

  it('completeStreamJob does not revive terminal status', async () => {
    const c = new AbortController()
    registerStreamJob('s4', c)
    await stopStreamJob('s4')
    // stop already set aborted — a late onDone must not flip to done
    completeStreamJob('s4', 'done')
    expect(countRunningJobs()).toBe(0)
  })
})
