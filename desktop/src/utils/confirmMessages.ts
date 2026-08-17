/** Copy for Remedy's own confirm dialogs (first-person, never browser-speak). */

import type { ConfirmRequest } from '../components/ConfirmDialog'

export const SESSION_DELETE_NOTE =
  'This removes the transcript plus session notes, attachments, plans, and '
  + 'undo history.\n\n'
  + 'What I remember about you (Partner Memory) is kept — clear that in '
  + 'Settings → You & Agent → Wipe persona.'

export function sessionDeleteConfirm(rawTitle: string): ConfirmRequest {
  const title = (rawTitle || '').trim() || 'this chat'
  return {
    title: `Delete “${title}”?`,
    body: SESSION_DELETE_NOTE,
    confirmLabel: 'Delete chat',
  }
}

export function sessionsDeleteConfirm(count: number): ConfirmRequest {
  const n = Math.max(0, Math.floor(count))
  return {
    title: n === 1 ? 'Delete 1 chat?' : `Delete ${n} chats?`,
    body: SESSION_DELETE_NOTE,
    confirmLabel: n === 1 ? 'Delete chat' : `Delete ${n} chats`,
  }
}

/** Removing a project folder is sidebar-only — files on disk are never touched. */
export function projectRemoveConfirm(
  projectKeyOrPath: string,
  sessionCount: number,
): ConfirmRequest {
  const name =
    projectKeyOrPath.split(/[/\\]/).filter(Boolean).pop() || projectKeyOrPath
  const n = Math.max(0, Math.floor(sessionCount))
  const chatNote =
    n > 0
      ? `Its ${n} chat${n === 1 ? '' : 's'} stay — they move to No project.`
      : 'It has no chats.'
  return {
    title: `Remove “${name}” from the sidebar?`,
    body:
      `${chatNote}\n\n`
      + 'Nothing on disk is touched — the folder and its files stay exactly '
      + 'where they are. You can add it back any time.',
    confirmLabel: 'Remove folder',
  }
}

export function skillDeleteConfirm(name: string): ConfirmRequest {
  return {
    title: `Delete skill “${name}”?`,
    body:
      'This removes it from me and deletes its folder under ~/.remedy/skills/.\n\n'
      + 'Bundled skills can’t be deleted this way, and library skills can be '
      + 'reinstalled later.',
    confirmLabel: 'Delete skill',
  }
}
