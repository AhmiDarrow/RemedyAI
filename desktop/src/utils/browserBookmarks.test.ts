import { beforeEach, describe, expect, it } from 'vitest'
import {
  addBookmark,
  isBookmarked,
  loadBookmarks,
  removeBookmark,
  titleFromUrl,
  toggleBookmark,
} from './browserBookmarks'

beforeEach(() => {
  const store = new Map<string, string>()
  globalThis.localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v)
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: () => null,
    length: 0,
  }
})

describe('browserBookmarks', () => {
  it('adds and loads bookmarks', () => {
    addBookmark('https://example.com/path', 'Example')
    const list = loadBookmarks()
    expect(list).toHaveLength(1)
    expect(list[0]!.url).toBe('https://example.com/path')
    expect(list[0]!.title).toBe('Example')
    expect(isBookmarked('https://example.com/path')).toBe(true)
  })

  it('toggles and removes', () => {
    toggleBookmark('github.com')
    expect(isBookmarked('https://github.com')).toBe(true)
    toggleBookmark('https://github.com')
    expect(isBookmarked('https://github.com')).toBe(false)
    addBookmark('https://x.ai')
    removeBookmark('https://x.ai')
    expect(loadBookmarks()).toHaveLength(0)
  })

  it('titleFromUrl strips www', () => {
    expect(titleFromUrl('https://www.example.com/a')).toBe('example.com/a')
  })

  it('ignores junk urls', () => {
    expect(addBookmark('javascript:alert(1)')).toHaveLength(0)
    expect(addBookmark('not a url')).toHaveLength(0)
  })

  it('refuses to persist bookmarks with URL userinfo credentials', () => {
    expect(addBookmark('https://alice:secret@example.com/a')).toHaveLength(0)
    expect(isBookmarked('https://alice:secret@example.com/a')).toBe(false)
    // Legitimate host still works
    addBookmark('https://example.com/safe')
    expect(isBookmarked('https://example.com/safe')).toBe(true)
  })

  it('drops poisoned localStorage entries that carry userinfo', () => {
    localStorage.setItem(
      'remedy.browserBookmarks.v1',
      JSON.stringify([
        {
          id: 'bad',
          title: 'leak',
          url: 'https://user:pass@evil.example/',
          createdAt: 1,
        },
        {
          id: 'good',
          title: 'ok',
          url: 'https://example.com/',
          createdAt: 2,
        },
      ]),
    )
    const list = loadBookmarks()
    expect(list).toHaveLength(1)
    expect(list[0]!.url).toBe('https://example.com/')
  })
})
