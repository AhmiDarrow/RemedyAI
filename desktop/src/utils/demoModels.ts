/** Curated Demo (llm7 guest) chat models — keep in sync with backend PROVIDER_CATALOG.demo */

export const DEMO_MODEL_ALLOWLIST = [
  'codestral-latest',
  'gemini-3.1-flash-lite',
  'gpt-oss:20b',
] as const

export const DEMO_DEFAULT_MODEL = DEMO_MODEL_ALLOWLIST[0]

/** Full curated options for pickers (always show all three). */
export const DEMO_MODEL_OPTIONS: { id: string; name: string }[] = [
  { id: 'codestral-latest', name: 'Codestral demo' },
  { id: 'gemini-3.1-flash-lite', name: 'Gemini Flash Lite demo' },
  { id: 'gpt-oss:20b', name: 'GPT-OSS 20B demo' },
]

const DEMO_BLOCK_RE =
  /image|flux|kling|seedance|veo|dall|sdxl|stable-diffusion|midjourney|video|tts|whisper|embed|moderation|omni/i

export function isDemoModelAllowed(id: string | null | undefined): boolean {
  const m = (id || '').trim()
  if (!m) return false
  if (DEMO_BLOCK_RE.test(m)) return false
  return (DEMO_MODEL_ALLOWLIST as readonly string[]).includes(m)
}

export function coerceDemoModel(id: string | null | undefined): string {
  return isDemoModelAllowed(id) ? String(id).trim() : DEMO_DEFAULT_MODEL
}

/** Prefer catalog names; always return the full curated set for Demo pickers. */
export function demoModelOptions(
  extra?: { id: string; name?: string }[] | null,
): { id: string; name: string }[] {
  const names = new Map(DEMO_MODEL_OPTIONS.map((m) => [m.id, m.name]))
  for (const m of extra || []) {
    if (m?.id && isDemoModelAllowed(m.id) && m.name) {
      names.set(m.id, m.name)
    }
  }
  return DEMO_MODEL_ALLOWLIST.map((id) => ({
    id,
    name: names.get(id) || id,
  }))
}
