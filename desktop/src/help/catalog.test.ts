import { describe, expect, it } from 'vitest'
import {
  HELP_ARTICLES,
  getArticle,
  resolveWikiHref,
  searchArticles,
  articlesByCategory,
} from './catalog'

describe('help catalog', () => {
  it('bundles all expected chapters with non-empty bodies', () => {
    expect(HELP_ARTICLES.length).toBeGreaterThanOrEqual(15)
    for (const a of HELP_ARTICLES) {
      expect(a.body.length).toBeGreaterThan(80)
      expect(a.body).toMatch(/^#\s/)
    }
  })

  it('resolves wiki hrefs', () => {
    expect(resolveWikiHref('02-first-run')).toBe('02-first-run')
    expect(resolveWikiHref('./09-troubleshooting.md')).toBe('09-troubleshooting')
    expect(resolveWikiHref('https://example.com')).toBeNull()
    expect(getArticle('11-reference-commands')?.title).toMatch(/command/i)
  })

  it('searches by tag and title', () => {
    const oauth = searchArticles('oauth xai')
    expect(oauth.some((a) => a.id === '03-providers-and-auth')).toBe(true)
    const fix = searchArticles('defender')
    expect(fix.some((a) => a.id === '09-troubleshooting')).toBe(true)
  })

  it('groups by category', () => {
    const groups = articlesByCategory()
    expect(groups.map((g) => g.category)).toContain('Start here')
    expect(groups.map((g) => g.category)).toContain('Reference')
  })
})
