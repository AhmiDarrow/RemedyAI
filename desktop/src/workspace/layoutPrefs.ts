import { ALL_SLIDES, type SlideId } from './types'

export type WorkspaceLayout = {
  left: SlideId
  right: SlideId
  leftWidth: number
  rightWidth: number
  leftOpen: boolean
  rightOpen: boolean
}

/** v2: true three-column shell; migrates off broken v1 nested-right layouts. */
const KEY = 'remedy.workspaceLayout.v2'
const LEGACY_KEY = 'remedy.workspaceLayout.v1'

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

function parseLayout(raw: string | null): WorkspaceLayout | null {
  if (!raw) return null
  try {
    const p = JSON.parse(raw) as Partial<WorkspaceLayout>
    return {
      left: coerceSlideId(p.left, DEFAULTS.left),
      right: coerceSlideId(p.right, DEFAULTS.right),
      leftWidth: clampWidth(p.leftWidth, DEFAULTS.leftWidth),
      rightWidth: clampWidth(p.rightWidth, DEFAULTS.rightWidth),
      leftOpen: p.leftOpen !== false,
      // Right starts collapsed by default; only true if explicitly saved open
      rightOpen: Boolean(p.rightOpen),
    }
  } catch {
    return null
  }
}

export function loadWorkspaceLayout(): WorkspaceLayout {
  try {
    const v2 = parseLayout(localStorage.getItem(KEY))
    if (v2) return v2
    // One-shot migrate widths/slides from v1; force right closed (layout was broken)
    const v1 = parseLayout(localStorage.getItem(LEGACY_KEY))
    if (v1) {
      const migrated: WorkspaceLayout = { ...v1, rightOpen: false }
      saveWorkspaceLayout(migrated)
      return migrated
    }
    return { ...DEFAULTS }
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
