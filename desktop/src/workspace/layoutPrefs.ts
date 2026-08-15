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

/** Side panel body width (px) — drag clamp shared by WorkspaceSide / SlideChrome. */
export const RAIL_WIDTH_MIN = 200
/** ~30% past the old 480px cap so Browser/Settings can open wider. */
export const RAIL_WIDTH_MAX = 624

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

const OAUTH_BROWSER_MIN = 440

/** Agent / computer-use: Browser takes the right rail (may replace Settings). */
export function layoutOpenBrowserInRail(prev: WorkspaceLayout): WorkspaceLayout {
  const next: WorkspaceLayout = {
    ...prev,
    right: 'browser',
    rightOpen: true,
    rightRail: 'open',
    rightWidth: Math.max(prev.rightWidth || 0, OAUTH_BROWSER_MIN),
    leftOpen: prev.leftRail === 'open' || prev.leftOpen,
  }
  if (next.left === 'browser') {
    next.left = 'sessions'
    next.leftRail = 'open'
    next.leftOpen = true
  }
  return next
}

/** OAuth: open Browser without unmounting Settings so the form can update. */
export function layoutOpenBrowserBesideSettings(prev: WorkspaceLayout): WorkspaceLayout {
  if (prev.left === 'settings') {
    return {
      ...prev,
      right: 'browser',
      rightRail: 'open',
      rightOpen: true,
      rightWidth: Math.max(prev.rightWidth || 0, OAUTH_BROWSER_MIN),
      left: 'settings',
      leftRail: 'open',
      leftOpen: true,
    }
  }
  return {
    ...prev,
    left: 'browser',
    leftRail: 'open',
    leftOpen: true,
    leftWidth: Math.max(prev.leftWidth || 0, OAUTH_BROWSER_MIN),
    right: 'settings',
    rightRail: 'open',
    rightOpen: true,
    rightWidth: Math.max(prev.rightWidth || 0, 300),
  }
}

export function coerceRailMode(value: unknown, fallback: RailMode): RailMode {
  if (typeof value === 'string' && RAIL_SET.has(value)) {
    return value as RailMode
  }
  return fallback
}

/** Clamp a side-panel width to the shared min/max. */
export function clampRailWidth(n: unknown, fallback: number = DEFAULTS.leftWidth): number {
  const v = Number(n)
  if (!Number.isFinite(v)) return fallback
  return Math.min(RAIL_WIDTH_MAX, Math.max(RAIL_WIDTH_MIN, Math.floor(v)))
}

function clampWidth(n: unknown, fallback: number): number {
  return clampRailWidth(n, fallback)
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
