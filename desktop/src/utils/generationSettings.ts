/** Reasoning effort / context settings: parsing and patch building for the
 * Settings panel. Mirrors the server-side validation so mistakes surface
 * before a save, and the server still has the final word. */

export const EFFORTS = ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'] as const
export type Effort = (typeof EFFORTS)[number]

export const LIMITS = {
  thinking_budget_tokens: [1024, 128_000],
  max_output_tokens: [256, 256_000],
  context_window_tokens: [4096, 2_000_000],
} as const
export type TokenKey = keyof typeof LIMITS

export interface GenerationForm {
  reasoning_effort: Effort
  thinking_budget_tokens: string
  max_output_tokens: string
  context_window_tokens: string
}

export interface GenerationValues {
  reasoning_effort?: string
  thinking_budget_tokens?: number
  max_output_tokens?: number
  context_window_tokens?: number
}

export const EMPTY_FORM: GenerationForm = {
  reasoning_effort: '',
  thinking_budget_tokens: '',
  max_output_tokens: '',
  context_window_tokens: '',
}

const num = (v: unknown): string => {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? String(Math.trunc(n)) : ''
}

export function formFromValues(v: GenerationValues | undefined | null): GenerationForm {
  const e = String(v?.reasoning_effort ?? '').toLowerCase()
  return {
    reasoning_effort: (EFFORTS as readonly string[]).includes(e) ? (e as Effort) : '',
    thinking_budget_tokens: num(v?.thinking_budget_tokens),
    max_output_tokens: num(v?.max_output_tokens),
    context_window_tokens: num(v?.context_window_tokens),
  }
}

/** Returns an error message for one token field, or null when valid/empty. */
export function tokenError(key: TokenKey, raw: string): string | null {
  const s = raw.trim().replace(/[_,]/g, '')
  if (!s) return null
  const n = Number(s)
  const [lo, hi] = LIMITS[key]
  if (!Number.isInteger(n) || n < lo || n > hi) return `Use a whole number from ${lo} to ${hi}, or leave empty.`
  return null
}

export function valuesFromForm(f: GenerationForm): GenerationValues {
  const tok = (raw: string) => {
    const s = raw.trim().replace(/[_,]/g, '')
    return s ? Number(s) : 0
  }
  return {
    reasoning_effort: f.reasoning_effort,
    thinking_budget_tokens: tok(f.thinking_budget_tokens),
    max_output_tokens: tok(f.max_output_tokens),
    context_window_tokens: tok(f.context_window_tokens),
  }
}

export function modelKey(provider: string, model: string): string {
  const p = provider.trim()
  const m = model.trim()
  return p && m ? `${p}/${m}` : m
}

/** Settings patch for one save. Empty fields send 0 / "" which clears them. */
export function buildGenerationPatch(
  f: GenerationForm,
  scope: 'all' | 'model',
  provider: string,
  model: string,
): GenerationValues & { model_settings?: Record<string, GenerationValues> } {
  const values = valuesFromForm(f)
  if (scope === 'model') {
    const key = modelKey(provider, model)
    if (!key) return {}
    return { model_settings: { [key]: values } }
  }
  return { ...values }
}
