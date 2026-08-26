import { describe, expect, it } from 'vitest'
import { looksLikeBuildKick, looksLikeLeaveChat } from './buildKick'

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

describe('looksLikeLeaveChat', () => {
  it('leaves Chat only on an explicit work ask', () => {
    expect(looksLikeLeaveChat('switch to build')).toBe(true)
    expect(looksLikeLeaveChat('please implement login')).toBe(true)
    expect(looksLikeLeaveChat('start working on it')).toBe(true)
  })

  it('does not treat continue as leaving Chat', () => {
    expect(looksLikeLeaveChat('continue')).toBe(false)
    expect(looksLikeLeaveChat('sounds good')).toBe(false)
  })

  it('leaves Chat on a clear keep-going work kick', () => {
    expect(looksLikeLeaveChat('keep going')).toBe(true)
    expect(looksLikeLeaveChat('proceed')).toBe(true)
    expect(looksLikeLeaveChat('go ahead')).toBe(true)
  })
})
