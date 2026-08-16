import { describe, expect, it } from 'vitest'
import { rustBrowserActionOk } from './useComputerHost'

describe('rustBrowserActionOk', () => {
  it('accepts only ok: prefixes', () => {
    expect(rustBrowserActionOk('ok:clicked')).toBe(true)
    expect(rustBrowserActionOk('ok')).toBe(true)
    expect(rustBrowserActionOk('ok-fallback')).toBe(true)
    expect(rustBrowserActionOk('browser:type:ok')).toBe(true)
    expect(rustBrowserActionOk('missing-ref:e1')).toBe(false)
    expect(rustBrowserActionOk('no-match:Membership')).toBe(false)
    expect(rustBrowserActionOk('no element')).toBe(false)
    expect(rustBrowserActionOk(true)).toBe(false)
  })
})
