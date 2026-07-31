import { describe, expect, it } from 'vitest'
import {
  DEFAULT_BROWSER_HOME,
  browserSearchUrl,
  isOpenableBrowserUrl,
  normalizeBrowserUrl,
  resolveBrowserAddressBar,
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

  it('rejects URLs with embedded credentials (userinfo)', () => {
    expect(normalizeBrowserUrl('https://user:token@example.com/path')).toBe('')
    expect(normalizeBrowserUrl('https://user@example.com')).toBe('')
    expect(normalizeBrowserUrl('http://:pass@localhost:8080')).toBe('')
    // Empty userinfo still blocked (parity with SSRF policy)
    expect(normalizeBrowserUrl('https://@evil.example')).toBe('')
    expect(isOpenableBrowserUrl('https://user:pass@x.com')).toBe(false)
  })

  it('rejects task-text leaks (spaces / emails / prose)', () => {
    expect(
      normalizeBrowserUrl(
        'gmail sign in, once there type user@example.com',
      ),
    ).toBe('')
    expect(normalizeBrowserUrl('user@example.com')).toBe('')
    expect(
      normalizeBrowserUrl(
        'https:gmail, in the login inout my username x@y.com',
      ),
    ).toBe('')
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
    // Soak/placeholder must not stick as homepage
    expect(resolveBrowserHome('https://example.com')).toBe(DEFAULT_BROWSER_HOME)
    expect(resolveBrowserHome('https://example.com/')).toBe(DEFAULT_BROWSER_HOME)
  })

  it('omnibox: bare hosts stay URLs; prose becomes DuckDuckGo search', () => {
    expect(resolveBrowserAddressBar('github.com')).toBe('https://github.com')
    expect(resolveBrowserAddressBar('https://x.ai/path')).toBe('https://x.ai/path')
    expect(resolveBrowserAddressBar('weather tokyo')).toBe(
      'https://duckduckgo.com/?q=weather%20tokyo',
    )
    expect(resolveBrowserAddressBar('remedy ai')).toContain('duckduckgo.com')
    expect(resolveBrowserAddressBar('javascript:alert(1)')).toBe('')
    expect(browserSearchUrl('a & b')).toBe('https://duckduckgo.com/?q=a%20%26%20b')
  })
})
