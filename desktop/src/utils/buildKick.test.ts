import { describe, expect, it } from 'vitest'
import { looksLikeBuildKick } from './buildKick'

describe('looksLikeBuildKick', () => {
  it('detects proceed / implement kicks', () => {
    expect(looksLikeBuildKick('proceed with all fixes')).toBe(true)
    expect(looksLikeBuildKick('please implement login')).toBe(true)
    expect(looksLikeBuildKick('switch to build mode')).toBe(true)
  })

  it('ignores pure questions', () => {
    expect(looksLikeBuildKick('what is plan mode?')).toBe(false)
    expect(looksLikeBuildKick('')).toBe(false)
  })
})
