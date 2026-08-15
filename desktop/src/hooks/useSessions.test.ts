import { describe, expect, it } from 'vitest'
import { resolveActiveAfterRefresh } from './useSessions'

describe('resolveActiveAfterRefresh', () => {
  it('keeps the focused id when it is on page 0', () => {
    expect(resolveActiveAfterRefresh('cur', ['a', 'cur', 'b'])).toBe('keep')
  })

  it('picks first when nothing is focused', () => {
    expect(resolveActiveAfterRefresh(null, ['a', 'b'])).toBe('first')
  })

  it('fetches the focused older chat instead of switching to page.sessions[0]', () => {
    expect(resolveActiveAfterRefresh('old-tab', ['newest'])).toBe('fetch')
  })
})
