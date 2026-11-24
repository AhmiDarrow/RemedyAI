import { describe, expect, it } from 'vitest'
import { mergeSessionPages, resolveActiveAfterRefresh } from './useSessions'

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

describe('mergeSessionPages', () => {
  const row = (id: string) => ({ id, title: id })

  it('keeps older-page rows that the new page omitted', () => {
    const out = mergeSessionPages([row('new')], [row('new'), row('old')], new Set())
    expect(out.map((s) => s.id)).toEqual(['new', 'old'])
  })

  it('does not restore a tombstoned session from a stale page', () => {
    const out = mergeSessionPages(
      [row('gone'), row('keep')],
      [row('keep')],
      new Set(['gone']),
    )
    expect(out.map((s) => s.id)).toEqual(['keep'])
  })

  it('does not keep a tombstoned row as a pagination extra', () => {
    const out = mergeSessionPages([row('keep')], [row('gone'), row('keep')], new Set(['gone']))
    expect(out.map((s) => s.id)).toEqual(['keep'])
  })
})
