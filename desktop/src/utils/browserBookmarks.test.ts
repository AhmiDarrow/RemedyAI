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
  // @ts-expect-error test mock
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
})
