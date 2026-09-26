import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import { headingAnchor } from './headingAnchor'

describe('offline help anchors', () => {
  it('matches manual punctuation and case', () => {
    expect(headingAnchor('What’s new (start here)')).toBe('whats-new-start-here')
    expect(headingAnchor('Always-ready desktop')).toBe('always-ready-desktop')
  })
  it('preserves non-English headings and inline formatting text', () => {
    expect(headingAnchor(['Install ', createElement('code', null, 'Linux')])).toBe('install-linux')
    expect(headingAnchor('日本語 設定')).toBe('日本語-設定')
  })
})
