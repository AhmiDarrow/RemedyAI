/**
 * Fonts by role — each role is its own setting.
 *
 * Remedy Sans UI is the primary face (all text unless a role says otherwise).
 * The other bundled faces each have a natural home:
 *   headings → Remedy Sans (geometric, made for titles)
 *   reading  → AhmiPrint (slab serif for long-form pages like Help)
 *   accent   → Ahmi Pixel (small brand touches: signature, shortcut chips)
 *   code     → AhmiPrint (same-width figures; also Cascadia Code, JetBrains Mono, Fira Code)
 * Every face ships inside the app (src/fonts.css), so it looks the same on
 * every machine and works offline. CJK and other scripts fall through to
 * the system fonts at the end of each stack.
 */

export type FontRole = 'ui' | 'heading' | 'reading' | 'accent' | 'code'

export type FontFaceId =
  | 'remedy-sans-ui'
  | 'remedy-sans'
  | 'ahmi-print'
  | 'ahmi-pixel'
  | 'inter'
  | 'cascadia-code'
  | 'jetbrains-mono'
  | 'fira-code'
  | 'system'
  | 'system-mono'

const CJK_FALLBACK =
  "'Segoe UI', 'Yu Gothic UI', 'Yu Gothic', 'Meiryo', 'Malgun Gothic', 'Microsoft YaHei', 'PingFang SC', " +
  "'Noto Sans CJK', 'Noto Sans SC', 'Noto Sans JP', 'Noto Sans KR', sans-serif"
const SANS = `system-ui, -apple-system, ${CJK_FALLBACK}`
const MONO = 'ui-monospace, monospace'

export const FONT_FACES: { id: FontFaceId; label: string; stack: string; mono?: boolean }[] = [
  { id: 'remedy-sans-ui', label: 'Remedy Sans UI', stack: `'Remedy Sans UI', ${SANS}` },
  { id: 'remedy-sans', label: 'Remedy Sans', stack: `'Remedy Sans', ${SANS}` },
  { id: 'ahmi-print', label: 'AhmiPrint', stack: `'AhmiPrint', Georgia, ${CJK_FALLBACK}` },
  { id: 'ahmi-pixel', label: 'Ahmi Pixel', stack: `'Ahmi Pixel', ${SANS}` },
  { id: 'inter', label: 'Inter', stack: `'Inter', ${SANS}` },
  { id: 'cascadia-code', label: 'Cascadia Code', stack: `'Cascadia Code', ${MONO}`, mono: true },
  { id: 'jetbrains-mono', label: 'JetBrains Mono', stack: `'JetBrains Mono', ${MONO}`, mono: true },
  { id: 'fira-code', label: 'Fira Code', stack: `'Fira Code', ${MONO}`, mono: true },
  { id: 'system', label: 'System font', stack: SANS },
  { id: 'system-mono', label: 'System monospace', stack: MONO, mono: true },
]

export const FONT_ROLES: { role: FontRole; label: string; hint: string; fallback: FontFaceId }[] = [
  { role: 'ui', label: 'Text', hint: 'Chat, menus and settings — the primary font.', fallback: 'remedy-sans-ui' },
  { role: 'heading', label: 'Headings', hint: 'Page and section titles.', fallback: 'remedy-sans' },
  { role: 'reading', label: 'Reading', hint: 'Long-form pages such as Help articles.', fallback: 'ahmi-print' },
  { role: 'accent', label: 'Accent', hint: 'Small touches: the signature and shortcut keys.', fallback: 'ahmi-pixel' },
  { role: 'code', label: 'Code', hint: 'Code blocks, diffs and logs.', fallback: 'ahmi-print' },
]

export type FontChoices = Record<FontRole, FontFaceId>

export const DEFAULT_FONTS: FontChoices = {
  ui: 'remedy-sans-ui',
  heading: 'remedy-sans',
  reading: 'ahmi-print',
  accent: 'ahmi-pixel',
  code: 'ahmi-print',
}

const KEY = 'remedy-fonts'

function isFace(v: unknown): v is FontFaceId {
  return typeof v === 'string' && FONT_FACES.some((f) => f.id === v)
}

/** Faces offered for a role: code leads with AhmiPrint, then monospace faces; others get text faces first. */
export function facesForRole(role: FontRole) {
  const mono = FONT_FACES.filter((f) => f.mono)
  const text = FONT_FACES.filter((f) => !f.mono)
  if (role === 'code') {
    const print = text.filter((f) => f.id === 'ahmi-print')
    const pixel = text.filter((f) => f.id === 'ahmi-pixel')
    return [...print, ...mono, ...pixel]
  }
  return [...text, ...mono]
}

export function loadFonts(): FontChoices {
  const out: FontChoices = { ...DEFAULT_FONTS }
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}') as Record<string, unknown>
    for (const r of FONT_ROLES) {
      if (isFace(raw[r.role])) out[r.role] = raw[r.role] as FontFaceId
    }
  } catch {
    // missing or corrupt: defaults
  }
  return out
}

export function fontStack(id: FontFaceId): string {
  return (FONT_FACES.find((f) => f.id === id) ?? FONT_FACES[0]).stack
}

/** Point the --font-<role> variables at the chosen stacks. */
export function applyFonts(choices: FontChoices = loadFonts()): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement.style
  for (const r of FONT_ROLES) {
    root.setProperty(`--font-${r.role}`, fontStack(choices[r.role]))
  }
}

export function saveFont(role: FontRole, face: FontFaceId): FontChoices {
  const next = { ...loadFonts(), [role]: face }
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // storage unavailable: still applies for this session
  }
  applyFonts(next)
  return next
}

export function resetFonts(): FontChoices {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // ignore
  }
  applyFonts(DEFAULT_FONTS)
  return { ...DEFAULT_FONTS }
}
