import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_AUTO_ARCHIVE_DAYS,
  getAllSessionMeta,
  isSessionArchived,
  setSessionMeta,
  toggleSessionArchive,
  toggleSessionPin,
} from './sessionMeta'

function installMemoryLocalStorage() {
  const store = new Map<string, string>()
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v))
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: ls,
    configurable: true,
    writable: true,
  })
}

describe('sessionMeta archive rules', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    localStorage.clear()
  })

  it('does not archive pinned sessions', () => {
    setSessionMeta('s1', { pinned: true, archived: true })
    expect(
      isSessionArchived(
        { id: 's1', updated_at: '2020-01-01T00:00:00.000Z' },
        getAllSessionMeta()['s1'],
      ),
    ).toBe(false)
  })

  it('archives when manually flagged', () => {
    setSessionMeta('s2', { archived: true })
    expect(
      isSessionArchived(
        { id: 's2', updated_at: new Date().toISOString() },
        getAllSessionMeta()['s2'],
      ),
    ).toBe(true)
  })

  it('auto-archives old non-open sessions', () => {
    const old = new Date(Date.now() - (DEFAULT_AUTO_ARCHIVE_DAYS + 2) * 864e5).toISOString()
    expect(
      isSessionArchived({ id: 's3', updated_at: old }, {}, { openIds: new Set() }),
    ).toBe(true)
  })

  it('keeps open sessions out of auto-archive', () => {
    const old = new Date(Date.now() - (DEFAULT_AUTO_ARCHIVE_DAYS + 2) * 864e5).toISOString()
    expect(
      isSessionArchived(
        { id: 's4', updated_at: old },
        {},
        { openIds: new Set(['s4']) },
      ),
    ).toBe(false)
  })

  it('toggle archive and pin clear archive', () => {
    expect(toggleSessionArchive('s5')).toBe(true)
    expect(getAllSessionMeta()['s5']?.archived).toBe(true)
    toggleSessionPin('s5')
    expect(getAllSessionMeta()['s5']?.pinned).toBe(true)
    expect(getAllSessionMeta()['s5']?.archived).toBe(false)
  })
})
