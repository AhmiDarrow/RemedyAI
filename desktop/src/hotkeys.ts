/**
 * Single source of truth for keyboard shortcuts (UI help + wiring).
 */

export type HotkeyScope = 'global' | 'composer'

export interface HotkeyDef {
  /** Display string, e.g. "Ctrl+N" or "Shift+Enter" */
  keys: string
  scope: HotkeyScope
  action: string
  /** For useKeyboardShortcuts matching */
  match?: {
    key: string
    ctrl?: boolean
    shift?: boolean
    alt?: boolean
  }
}

export const HOTKEYS: HotkeyDef[] = [
  {
    keys: 'Enter',
    scope: 'composer',
    action: 'Send message',
  },
  {
    keys: 'Shift+Enter',
    scope: 'composer',
    action: 'Insert a new line',
  },
  {
    keys: 'Ctrl+N',
    scope: 'global',
    action: 'New chat session',
    match: { key: 'n', ctrl: true },
  },
  {
    keys: 'Ctrl+P',
    scope: 'global',
    action: 'Open command palette',
    match: { key: 'p', ctrl: true },
  },
  {
    keys: 'Ctrl+K',
    scope: 'global',
    action: 'Open command palette',
    match: { key: 'k', ctrl: true },
  },
  {
    keys: 'Ctrl+B',
    scope: 'global',
    action: 'Toggle plan mode',
    match: { key: 'b', ctrl: true },
  },
  {
    keys: 'Ctrl+,',
    scope: 'global',
    action: 'Open settings',
    match: { key: ',', ctrl: true },
  },
  {
    keys: 'Ctrl+/',
    scope: 'global',
    action: 'Show keyboard shortcuts',
    match: { key: '/', ctrl: true },
  },
  {
    keys: 'F1',
    scope: 'global',
    action: 'Show keyboard shortcuts',
    match: { key: 'F1', ctrl: false },
  },
  {
    keys: 'Escape',
    scope: 'global',
    action: 'Close panels and command palette',
    match: { key: 'Escape', ctrl: false },
  },
]

export function formatHotkeysHelpText(): string {
  const lines = ['**Keyboard shortcuts**', '']
  for (const h of HOTKEYS) {
    const where = h.scope === 'composer' ? 'Composer' : 'App'
    lines.push(`  \`${h.keys}\` — ${h.action} _(${where})_`)
  }
  lines.push('')
  lines.push('Tip: use **Shift+Enter** for multi-line messages.')
  return lines.join('\n')
}

export function formatHotkeysPlain(): string {
  return HOTKEYS.map((h) => `  ${h.keys.padEnd(14)} ${h.action}`).join('\n')
}
