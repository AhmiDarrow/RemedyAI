import { beforeEach, describe, expect, it } from 'vitest'
import {
  coerceSlideId,
  loadWorkspaceLayout,
  saveWorkspaceLayout,
} from './layoutPrefs'

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

describe('workspace layoutPrefs v2', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    localStorage.clear()
  })

  it('returns defaults when empty (right collapsed)', () => {
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

  it('rejects unknown slide ids (prevents SLIDE_META crash)', () => {
    expect(coerceSlideId('not-a-slide', 'sessions')).toBe('sessions')
    expect(coerceSlideId('browser', 'sessions')).toBe('browser')
    localStorage.setItem(
      'remedy.workspaceLayout.v2',
      JSON.stringify({ left: 'bogus', right: 42, leftWidth: 'x' }),
    )
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('sessions')
    expect(L.right).toBe('settings')
    expect(L.leftWidth).toBe(280)
  })

  it('migrates v1 prefs and forces right closed', () => {
    localStorage.setItem(
      'remedy.workspaceLayout.v1',
      JSON.stringify({
        left: 'settings',
        right: 'terminal',
        leftWidth: 300,
        rightWidth: 320,
        leftOpen: true,
        rightOpen: true,
      }),
    )
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('settings')
    expect(L.right).toBe('terminal')
    expect(L.rightOpen).toBe(false)
    // Persisted under v2
    expect(localStorage.getItem('remedy.workspaceLayout.v2')).toBeTruthy()
  })
})
