import { beforeEach, describe, expect, it } from 'vitest'
import {
  coerceRailMode,
  coerceSlideId,
  layoutOpenBrowserBesideSettings,
  layoutOpenBrowserInRail,
  layoutOpenSlideInRail,
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

describe('workspace layoutPrefs v3', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    localStorage.clear()
  })

  it('returns defaults when empty (left open, right thin)', () => {
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('sessions')
    expect(L.leftRail).toBe('open')
    expect(L.rightRail).toBe('thin')
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
      leftRail: 'icons',
      rightRail: 'open',
    })
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('files')
    expect(L.right).toBe('scratch')
    expect(L.leftWidth).toBe(200)
    expect(L.rightWidth).toBe(624)
    expect(L.leftRail).toBe('icons')
    expect(L.rightRail).toBe('open')
    expect(L.leftOpen).toBe(false)
    expect(L.rightOpen).toBe(true)
  })

  it('rejects unknown slide ids (prevents SLIDE_META crash)', () => {
    expect(coerceSlideId('not-a-slide', 'sessions')).toBe('sessions')
    expect(coerceSlideId('browser', 'sessions')).toBe('browser')
    expect(coerceRailMode('bogus', 'thin')).toBe('thin')
    expect(coerceRailMode('icons', 'thin')).toBe('icons')
    localStorage.setItem(
      'remedy.workspaceLayout.v3',
      JSON.stringify({ left: 'bogus', right: 42, leftWidth: 'x', leftRail: 'nope' }),
    )
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('sessions')
    expect(L.right).toBe('settings')
    expect(L.leftWidth).toBe(280)
    expect(L.leftRail).toBe('open') // coerceRailMode('nope') → leftOpen default true → open
  })

  it('migrates v2 prefs to v3 rails', () => {
    localStorage.setItem(
      'remedy.workspaceLayout.v2',
      JSON.stringify({
        left: 'settings',
        right: 'terminal',
        leftWidth: 300,
        rightWidth: 320,
        leftOpen: true,
        rightOpen: false,
      }),
    )
    const L = loadWorkspaceLayout()
    expect(L.left).toBe('settings')
    expect(L.right).toBe('terminal')
    expect(L.leftRail).toBe('open')
    expect(L.rightRail).toBe('thin')
    expect(localStorage.getItem('remedy.workspaceLayout.v3')).toBeTruthy()
  })

  it('opens Browser on the right and does not leave two browsers', () => {
    const next = layoutOpenBrowserInRail({
      left: 'sessions',
      right: 'settings',
      leftWidth: 280,
      rightWidth: 300,
      leftOpen: true,
      rightOpen: true,
      leftRail: 'open',
      rightRail: 'open',
    })
    expect(next.right).toBe('browser')
    expect(next.left).toBe('sessions')
    expect(next.rightRail).toBe('open')
  })

  it('keeps Settings mounted when opening Browser for OAuth', () => {
    const fromRight = layoutOpenBrowserBesideSettings({
      left: 'sessions',
      right: 'settings',
      leftWidth: 280,
      rightWidth: 320,
      leftOpen: true,
      rightOpen: true,
      leftRail: 'open',
      rightRail: 'open',
    })
    expect(fromRight.right).toBe('settings')
    expect(fromRight.left).toBe('browser')
    expect(fromRight.leftRail).toBe('open')
    expect(fromRight.rightRail).toBe('open')

    const fromLeft = layoutOpenBrowserBesideSettings({
      left: 'settings',
      right: 'terminal',
      leftWidth: 300,
      rightWidth: 320,
      leftOpen: true,
      rightOpen: false,
      leftRail: 'open',
      rightRail: 'thin',
    })
    expect(fromLeft.left).toBe('settings')
    expect(fromLeft.right).toBe('browser')
    expect(fromLeft.rightRail).toBe('open')
  })

  it('opens Terminal on the right without duplicating the slide', () => {
    const next = layoutOpenSlideInRail(
      {
        left: 'terminal',
        right: 'settings',
        leftWidth: 280,
        rightWidth: 300,
        leftOpen: true,
        rightOpen: false,
        leftRail: 'open',
        rightRail: 'thin',
      },
      'terminal',
    )
    expect(next.right).toBe('terminal')
    expect(next.left).toBe('sessions')
    expect(next.rightRail).toBe('open')
  })
})
