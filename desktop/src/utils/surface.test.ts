/** Surface preference: Grove default, Studio persists, corrupt values safe. */

import { beforeEach, describe, expect, it } from 'vitest'
import { loadSurface, saveSurface } from './surface'

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

describe('surface preference', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
  })

  it('defaults to grove for new owners', () => {
    expect(loadSurface()).toBe('grove')
  })

  it('persists studio when chosen', () => {
    saveSurface('studio')
    expect(loadSurface()).toBe('studio')
    saveSurface('grove')
    expect(loadSurface()).toBe('grove')
  })

  it('coerces corrupt stored values back to grove', () => {
    localStorage.setItem('remedy.surface', 'bananas')
    expect(loadSurface()).toBe('grove')
  })
})
