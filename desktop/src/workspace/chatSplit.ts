/** Studio chat / actions split — sibling of remedy.workspaceLayout.v3. */

export const CHAT_SPLIT_KEY = 'remedy.chatSplit.v1'

export type ChatActionsSide = 'left' | 'right'

export type ChatSplitPrefs = {
  /** Which side holds the coding/actions pane. Default left (Joe's sketch). */
  actionsSide: ChatActionsSide
  /** Actions pane width in px when the panes sit side by side. */
  actionsWidth: number
}

export const CHAT_ACTIONS_WIDTH_MIN = 240
export const CHAT_ACTIONS_WIDTH_MAX = 720
export const CHAT_ACTIONS_WIDTH_DEFAULT = 380
/** Chat pane never shrinks past this while the split is side-by-side. */
export const CHAT_PANE_MIN = 280
/** Stack or toggle when the feed column is narrower than this. */
export const CHAT_SPLIT_STACK_BELOW = 720
const SPLITTER = 8

const DEFAULTS: ChatSplitPrefs = {
  actionsSide: 'left',
  actionsWidth: CHAT_ACTIONS_WIDTH_DEFAULT,
}

export function coerceActionsSide(value: unknown, fallback: ChatActionsSide = 'left'): ChatActionsSide {
  return value === 'right' || value === 'left' ? value : fallback
}

export function clampChatActionsWidth(
  n: unknown,
  fallback: number = CHAT_ACTIONS_WIDTH_DEFAULT,
  containerWidth?: number,
): number {
  const v = Number(n)
  const raw = Number.isFinite(v) ? Math.floor(v) : fallback
  let max = CHAT_ACTIONS_WIDTH_MAX
  if (containerWidth != null && Number.isFinite(containerWidth)) {
    max = Math.min(
      CHAT_ACTIONS_WIDTH_MAX,
      Math.max(CHAT_ACTIONS_WIDTH_MIN, Math.floor(containerWidth) - CHAT_PANE_MIN - SPLITTER),
    )
  }
  return Math.min(max, Math.max(CHAT_ACTIONS_WIDTH_MIN, raw))
}

export function shouldStackChatSplit(containerWidth: number): boolean {
  if (!Number.isFinite(containerWidth)) return false
  return containerWidth < CHAT_SPLIT_STACK_BELOW
    || containerWidth < CHAT_ACTIONS_WIDTH_MIN + CHAT_PANE_MIN + SPLITTER
}

export function loadChatSplit(): ChatSplitPrefs {
  try {
    const raw = localStorage.getItem(CHAT_SPLIT_KEY)
    if (!raw) return { ...DEFAULTS }
    const p = JSON.parse(raw) as Partial<ChatSplitPrefs>
    return {
      actionsSide: coerceActionsSide(p.actionsSide, DEFAULTS.actionsSide),
      actionsWidth: clampChatActionsWidth(p.actionsWidth, DEFAULTS.actionsWidth),
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveChatSplit(prefs: ChatSplitPrefs) {
  try {
    const safe: ChatSplitPrefs = {
      actionsSide: coerceActionsSide(prefs.actionsSide, DEFAULTS.actionsSide),
      actionsWidth: clampChatActionsWidth(prefs.actionsWidth, DEFAULTS.actionsWidth),
    }
    localStorage.setItem(CHAT_SPLIT_KEY, JSON.stringify(safe))
  } catch {
    /* quota / private mode */
  }
}

export function swapChatSplitSides(prefs: ChatSplitPrefs): ChatSplitPrefs {
  return {
    ...prefs,
    actionsSide: prefs.actionsSide === 'left' ? 'right' : 'left',
  }
}
