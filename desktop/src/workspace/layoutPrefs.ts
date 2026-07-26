import { ALL_SLIDES, type SlideId } from './types'

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

const SLIDE_SET = new Set<string>(ALL_SLIDES)

/** Coerce untrusted / corrupted localStorage values to a known slide. */
export function coerceSlideId(value: unknown, fallback: SlideId): SlideId {
  if (typeof value === 'string' && SLIDE_SET.has(value)) {
    return value as SlideId
  }
  return fallback
}

function clampWidth(n: unknown, fallback: number): number {
  const v = Number(n)
  if (!Number.isFinite(v)) return fallback
  return Math.min(480, Math.max(200, Math.floor(v)))
}

export function loadWorkspaceLayout(): WorkspaceLayout {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULTS }
    const p = JSON.parse(raw) as Partial<WorkspaceLayout>
    return {
      left: coerceSlideId(p.left, DEFAULTS.left),
      right: coerceSlideId(p.right, DEFAULTS.right),
      leftWidth: clampWidth(p.leftWidth, DEFAULTS.leftWidth),
      rightWidth: clampWidth(p.rightWidth, DEFAULTS.rightWidth),
      leftOpen: p.leftOpen !== false,
      rightOpen: Boolean(p.rightOpen),
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveWorkspaceLayout(layout: WorkspaceLayout) {
  try {
    const safe: WorkspaceLayout = {
      left: coerceSlideId(layout.left, DEFAULTS.left),
      right: coerceSlideId(layout.right, DEFAULTS.right),
      leftWidth: clampWidth(layout.leftWidth, DEFAULTS.leftWidth),
      rightWidth: clampWidth(layout.rightWidth, DEFAULTS.rightWidth),
      leftOpen: Boolean(layout.leftOpen),
      rightOpen: Boolean(layout.rightOpen),
    }
    localStorage.setItem(KEY, JSON.stringify(safe))
  } catch {
    /* quota / private mode */
  }
}
