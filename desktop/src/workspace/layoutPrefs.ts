import { ALL_SLIDES, type SlideId } from './types'

/** Panel body open, icon rail only, or thin click strip. */
export type RailMode = 'open' | 'icons' | 'thin'

export type WorkspaceLayout = {
  left: SlideId
  right: SlideId
  leftWidth: number
  rightWidth: number
  /** @deprecated prefer leftRail — kept for migration */
  leftOpen: boolean
  /** @deprecated prefer rightRail — kept for migration */
  rightOpen: boolean
  leftRail: RailMode
  rightRail: RailMode
}

/** v3: thin | icons | open rails on both sides. */
const KEY = 'remedy.workspaceLayout.v3'
const LEGACY_V2 = 'remedy.workspaceLayout.v2'
const LEGACY_V1 = 'remedy.workspaceLayout.v1'

const DEFAULTS: WorkspaceLayout = {
  left: 'sessions',
  right: 'settings',
  leftWidth: 280,
  rightWidth: 300,
  leftOpen: true,
  rightOpen: false,
  leftRail: 'open',
  rightRail: 'thin',
}

const SLIDE_SET = new Set<string>(ALL_SLIDES)
const RAIL_SET = new Set<string>(['open', 'icons', 'thin'])

/** Coerce untrusted / corrupted localStorage values to a known slide. */
export function coerceSlideId(value: unknown, fallback: SlideId): SlideId {
  if (typeof value === 'string' && SLIDE_SET.has(value)) {
    return value as SlideId
  }
  return fallback
}

export function coerceRailMode(value: unknown, fallback: RailMode): RailMode {
  if (typeof value === 'string' && RAIL_SET.has(value)) {
    return value as RailMode
  }
  return fallback
}

function clampWidth(n: unknown, fallback: number): number {
  const v = Number(n)
  if (!Number.isFinite(v)) return fallback
  return Math.min(480, Math.max(200, Math.floor(v)))
}

function fromOpenFlags(leftOpen: boolean, rightOpen: boolean): Pick<WorkspaceLayout, 'leftRail' | 'rightRail' | 'leftOpen' | 'rightOpen'> {
  return {
    leftOpen,
    rightOpen,
    leftRail: leftOpen ? 'open' : 'icons',
    rightRail: rightOpen ? 'open' : 'thin',
  }
}

function parseLayout(raw: string | null): WorkspaceLayout | null {
  if (!raw) return null
  try {
    const p = JSON.parse(raw) as Partial<WorkspaceLayout> & {
      leftOpen?: boolean
      rightOpen?: boolean
    }
    const leftOpen = p.leftOpen !== false
    const rightOpen = Boolean(p.rightOpen)
    const rails =
      p.leftRail != null || p.rightRail != null
        ? {
            leftRail: coerceRailMode(p.leftRail, leftOpen ? 'open' : 'icons'),
            rightRail: coerceRailMode(p.rightRail, rightOpen ? 'open' : 'thin'),
            leftOpen: coerceRailMode(p.leftRail, leftOpen ? 'open' : 'icons') === 'open',
            rightOpen: coerceRailMode(p.rightRail, rightOpen ? 'open' : 'thin') === 'open',
          }
        : fromOpenFlags(leftOpen, rightOpen)
    return {
      left: coerceSlideId(p.left, DEFAULTS.left),
      right: coerceSlideId(p.right, DEFAULTS.right),
      leftWidth: clampWidth(p.leftWidth, DEFAULTS.leftWidth),
      rightWidth: clampWidth(p.rightWidth, DEFAULTS.rightWidth),
      ...rails,
    }
  } catch {
    return null
  }
}

export function loadWorkspaceLayout(): WorkspaceLayout {
  try {
    const v3 = parseLayout(localStorage.getItem(KEY))
    if (v3) return v3
    const v2 = parseLayout(localStorage.getItem(LEGACY_V2))
    if (v2) {
      // Migrate: keep left open/icons; right defaults thin when was closed
      const migrated: WorkspaceLayout = {
        ...v2,
        rightRail: v2.rightOpen ? 'open' : 'thin',
        leftRail: v2.leftOpen ? 'open' : 'icons',
      }
      saveWorkspaceLayout(migrated)
      return migrated
    }
    const v1 = parseLayout(localStorage.getItem(LEGACY_V1))
    if (v1) {
      const migrated: WorkspaceLayout = {
        ...v1,
        rightOpen: false,
        rightRail: 'thin',
        leftRail: v1.leftOpen ? 'open' : 'icons',
      }
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
    const leftRail = coerceRailMode(layout.leftRail, DEFAULTS.leftRail)
    const rightRail = coerceRailMode(layout.rightRail, DEFAULTS.rightRail)
    const safe: WorkspaceLayout = {
      left: coerceSlideId(layout.left, DEFAULTS.left),
      right: coerceSlideId(layout.right, DEFAULTS.right),
      leftWidth: clampWidth(layout.leftWidth, DEFAULTS.leftWidth),
      rightWidth: clampWidth(layout.rightWidth, DEFAULTS.rightWidth),
      leftRail,
      rightRail,
      leftOpen: leftRail === 'open',
      rightOpen: rightRail === 'open',
    }
    localStorage.setItem(KEY, JSON.stringify(safe))
  } catch {
    /* quota / private mode */
  }
}
