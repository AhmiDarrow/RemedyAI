import type { SlideId } from './types'

export type WorkspaceLayout = {
  left: SlideId
  right: SlideId
  leftWidth: number
  rightWidth: number
  leftOpen: boolean
  rightOpen: boolean
}

const KEY = 'remedy.workspaceLayout.v1'

const DEFAULTS: WorkspaceLayout = {
  left: 'sessions',
  right: 'settings',
  leftWidth: 280,
  rightWidth: 300,
  leftOpen: true,
  rightOpen: false,
}

export function loadWorkspaceLayout(): WorkspaceLayout {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULTS }
    const p = JSON.parse(raw) as Partial<WorkspaceLayout>
    return {
      left: p.left || DEFAULTS.left,
      right: p.right || DEFAULTS.right,
      leftWidth: Math.min(480, Math.max(200, Number(p.leftWidth) || DEFAULTS.leftWidth)),
      rightWidth: Math.min(480, Math.max(200, Number(p.rightWidth) || DEFAULTS.rightWidth)),
      leftOpen: p.leftOpen !== false,
      rightOpen: Boolean(p.rightOpen),
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveWorkspaceLayout(layout: WorkspaceLayout) {
  try {
    localStorage.setItem(KEY, JSON.stringify(layout))
  } catch {
    /* */
  }
}
