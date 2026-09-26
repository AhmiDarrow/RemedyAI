import { describe, expect, it } from 'vitest'
import { createAsyncScope } from './asyncScope'

describe('async view isolation', () => {
  it('rejects an old session response even after switching back', () => {
    const scope = createAsyncScope('a')
    const old = scope.latest()
    scope.setKey('b')
    const b = scope.latest()
    scope.setKey('a')
    expect(old()).toBe(false)
    expect(b()).toBe(false)
    expect(scope.latest()()).toBe(true)
  })
  it('only applies the latest overlapping response in a lane', () => {
    const scope = createAsyncScope('a')
    const first = scope.latest('plans')
    const second = scope.latest('plans')
    const independent = scope.latest('approvals')
    expect(first()).toBe(false)
    expect(second()).toBe(true)
    expect(independent()).toBe(true)
  })
  it('keeps an action valid through background polling, but not a view change', () => {
    const scope = createAsyncScope('a')
    const action = scope.capture()
    scope.latest()
    scope.latest()
    scope.setKey('a')
    expect(action()).toBe(true)
    scope.setKey(null)
    expect(action()).toBe(false)
  })
  it('invalidates pending reads and actions on unmount', () => {
    const scope = createAsyncScope('a')
    const read = scope.latest()
    const action = scope.capture()
    scope.invalidate()
    expect(read()).toBe(false)
    expect(action()).toBe(false)
  })
})
