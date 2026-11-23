import { beforeEach, describe, expect, it } from 'vitest'
import {
  CHAT_ACTIONS_WIDTH_DEFAULT,
  CHAT_ACTIONS_WIDTH_MAX,
  CHAT_ACTIONS_WIDTH_MIN,
  CHAT_PANE_MIN,
  CHAT_SPLIT_KEY,
  clampChatActionsWidth,
  coerceActionsSide,
  loadChatSplit,
  saveChatSplit,
  shouldStackChatSplit,
  swapChatSplitSides,
} from './chatSplit'

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

describe('chatSplit prefs', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    localStorage.clear()
  })

  it('defaults to actions on the left at a comfortable width', () => {
    const p = loadChatSplit()
    expect(p.actionsSide).toBe('left')
    expect(p.actionsWidth).toBe(CHAT_ACTIONS_WIDTH_DEFAULT)
  })

  it('round-trips save/load and clamps width', () => {
    saveChatSplit({ actionsSide: 'right', actionsWidth: 40 })
    expect(loadChatSplit()).toEqual({
      actionsSide: 'right',
      actionsWidth: CHAT_ACTIONS_WIDTH_MIN,
    })
    saveChatSplit({ actionsSide: 'left', actionsWidth: 9999 })
    expect(loadChatSplit().actionsWidth).toBe(CHAT_ACTIONS_WIDTH_MAX)
  })

  it('ignores a corrupt payload', () => {
    localStorage.setItem(CHAT_SPLIT_KEY, '{not json')
    expect(loadChatSplit().actionsSide).toBe('left')
    localStorage.setItem(CHAT_SPLIT_KEY, JSON.stringify({ actionsSide: 'up', actionsWidth: 'wide' }))
    const p = loadChatSplit()
    expect(p.actionsSide).toBe('left')
    expect(p.actionsWidth).toBe(CHAT_ACTIONS_WIDTH_DEFAULT)
  })

  it('swaps sides without changing width', () => {
    const next = swapChatSplitSides({ actionsSide: 'left', actionsWidth: 400 })
    expect(next).toEqual({ actionsSide: 'right', actionsWidth: 400 })
    expect(swapChatSplitSides(next).actionsSide).toBe('left')
  })

  it('coerces sides and stacks on a narrow column', () => {
    expect(coerceActionsSide('right')).toBe('right')
    expect(coerceActionsSide('nope')).toBe('left')
    expect(shouldStackChatSplit(500)).toBe(true)
    expect(shouldStackChatSplit(1100)).toBe(false)
    expect(shouldStackChatSplit(CHAT_ACTIONS_WIDTH_MIN + CHAT_PANE_MIN)).toBe(true)
  })

  it('clamps width so the chat pane keeps its minimum', () => {
    expect(clampChatActionsWidth(500, 380, 600)).toBe(600 - CHAT_PANE_MIN - 8)
    expect(clampChatActionsWidth(200, 380, 900)).toBe(CHAT_ACTIONS_WIDTH_MIN)
  })
})
