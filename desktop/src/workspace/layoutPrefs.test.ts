import { beforeEach, describe, expect, it } from 'vitest'
import { loadWorkspaceLayout, saveWorkspaceLayout } from './layoutPrefs'

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

describe('workspace layoutPrefs', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    localStorage.clear()
  })

  it('returns defaults when empty', () => {
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('sessions')
    expect(L.leftOpen).toBe(true)
    expect(L.rightOpen).toBe(false)
    expect(L.leftWidth).toBeGreaterThanOrEqual(200)
  })

  it('round-trips save/load and clamps widths', () => {
    saveWorkspaceLayout({
      left: 'files',
      right: 'scratch',
      leftWidth: 50,
      rightWidth: 9999,
      leftOpen: false,
      rightOpen: true,
    })
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('files')
    expect(L.right).toBe('scratch')
    expect(L.leftWidth).toBe(200)
    expect(L.rightWidth).toBe(480)
    expect(L.leftOpen).toBe(false)
    expect(L.rightOpen).toBe(true)
  })
})
