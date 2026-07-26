import { describe, expect, it } from 'vitest'
import {
  DEFAULT_BROWSER_HOME,
  isOpenableBrowserUrl,
  normalizeBrowserUrl,
  resolveBrowserHome,
} from './browserUrl'

describe('browserUrl', () => {
  it('prefixes https for bare hosts', () => {
    expect(normalizeBrowserUrl('example.com/path')).toBe('https://example.com/path')
    expect(normalizeBrowserUrl('  github.com  ')).toBe('https://github.com')
  })

  it('preserves http(s) and about:', () => {
    expect(normalizeBrowserUrl('http://localhost:3000')).toBe('http://localhost:3000')
    expect(normalizeBrowserUrl('https://x.ai')).toBe('https://x.ai')
    expect(normalizeBrowserUrl('about:blank')).toBe('about:blank')
  })

  it('blocks dangerous schemes', () => {
    expect(normalizeBrowserUrl('javascript:alert(1)')).toBe('')
    expect(normalizeBrowserUrl('data:text/html,hi')).toBe('')
    expect(normalizeBrowserUrl('file:///C:/Windows')).toBe('')
    expect(normalizeBrowserUrl('')).toBe('')
  })

  it('isOpenableBrowserUrl matches external open allowlist', () => {
    expect(isOpenableBrowserUrl('https://a.com')).toBe(true)
    expect(isOpenableBrowserUrl('http://a.com')).toBe(true)
    expect(isOpenableBrowserUrl('about:blank')).toBe(true)
    expect(isOpenableBrowserUrl('javascript:x')).toBe(false)
    expect(isOpenableBrowserUrl('file:///x')).toBe(false)
  })

  it('default home is Remedy GitHub; resolveBrowserHome falls back', () => {
    expect(DEFAULT_BROWSER_HOME).toBe('https://github.com/AhmiDarrow/RemedyAI')
    expect(resolveBrowserHome('')).toBe(DEFAULT_BROWSER_HOME)
    expect(resolveBrowserHome(null)).toBe(DEFAULT_BROWSER_HOME)
    expect(resolveBrowserHome('javascript:x')).toBe(DEFAULT_BROWSER_HOME)
    expect(resolveBrowserHome('docs.example.com')).toBe('https://docs.example.com')
  })
})
