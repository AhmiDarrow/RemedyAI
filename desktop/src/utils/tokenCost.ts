/** Client-side token/cost helpers (mirrors server remedy.core.usage). */

export type UsageSnapshot = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  estimated_cost_usd: number
  source?: 'provider' | 'estimate' | string
  model?: string | null
  provider?: string | null
}

// Order matters: more specific model ids first (grok-4.5 before grok-4).
const PRICE: Array<{ re: RegExp; pin: number; pout: number }> = [
  { re: /grok-4\.5|grok-4-5/i, pin: 2.0, pout: 6.0 },
  { re: /grok-4\.3/i, pin: 1.25, pout: 2.5 },
  { re: /grok-4(?!\.|\d)|grok-3(?!-mini)/i, pin: 3.0, pout: 15.0 },
  { re: /grok-3-mini|grok-2/i, pin: 0.3, pout: 0.5 },
  { re: /gpt-4o-mini/i, pin: 0.15, pout: 0.6 },
  { re: /gpt-4o|gpt-4\.1/i, pin: 2.5, pout: 10 },
  { re: /claude-3-5-sonnet|claude-sonnet/i, pin: 3, pout: 15 },
  { re: /claude-3-5-haiku|claude-3-haiku/i, pin: 0.8, pout: 4 },
  { re: /deepseek-v4-pro|deepseek-reasoner|deepseek-r1/i, pin: 0.55, pout: 2.19 },
  { re: /deepseek/i, pin: 0.14, pout: 0.28 },
  { re: /grok/i, pin: 1.0, pout: 3.0 },
  { re: /gemini/i, pin: 0.35, pout: 1.05 },
  { re: /demo|ollama/i, pin: 0, pout: 0 },
]

/** Align with NanoToken class-weighted heuristic (~4 chars/token prose baseline). */
export function estimateTokensText(text: string): number {
  if (!text) return 0
  let w = 0
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!
    const code = ch.charCodeAt(0)
    if (/\s/.test(ch)) w += 0.15
    else if (code > 0x2e80) w += 1.0 // rough CJK
    else if (/\d/.test(ch)) w += 0.3
    else if (/[a-zA-Z]/.test(ch)) w += 0.25
    else if (code < 128) w += 0.45 // punct/code
    else w += 0.35
  }
  return Math.max(0, Math.round(w))
}

export function pricePerMtok(model?: string | null, provider?: string | null): [number, number] {
  const blob = `${provider || ''} ${model || ''}`.trim()
  if ((provider || '').toLowerCase() === 'ollama' || (provider || '').toLowerCase() === 'demo') {
    return [0, 0]
  }
  for (const row of PRICE) {
    if (row.re.test(blob)) return [row.pin, row.pout]
  }
  return [1, 3]
}

export function estimateCostUsd(
  prompt: number,
  completion: number,
  model?: string | null,
  provider?: string | null,
): number {
  const [pin, pout] = pricePerMtok(model, provider)
  return (Math.max(0, prompt) * pin + Math.max(0, completion) * pout) / 1_000_000
}

export function formatCost(usd: number): string {
  if (!Number.isFinite(usd) || usd <= 0) return '$0.00'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  if (usd < 1) return `$${usd.toFixed(3)}`
  return `$${usd.toFixed(2)}`
}

export function formatTokens(n: number): string {
  const v = Math.max(0, Math.round(Number(n) || 0))
  if (v < 1000) return String(v)
  if (v < 10_000) return `${(v / 1000).toFixed(1)}k`
  return `${Math.round(v / 1000)}k`
}

export function emptyUsage(model?: string | null, provider?: string | null): UsageSnapshot {
  return {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    estimated_cost_usd: 0,
    source: 'estimate',
    model,
    provider,
  }
}

/** Live run estimate while streaming (until provider usage arrives). */
export function liveRunEstimate(
  partialText: string,
  partialThinking: string,
  model?: string | null,
  provider?: string | null,
  providerUsage?: UsageSnapshot | null,
): UsageSnapshot {
  if (providerUsage && providerUsage.source === 'provider' && (providerUsage.total_tokens || 0) > 0) {
    return providerUsage
  }
  const completion = estimateTokensText(`${partialThinking || ''}${partialText || ''}`)
  // Prompt is unknown mid-run without a provider frame — show completion as the live count.
  return {
    prompt_tokens: providerUsage?.prompt_tokens ?? 0,
    completion_tokens: Math.max(completion, providerUsage?.completion_tokens ?? 0),
    total_tokens: Math.max(
      completion + (providerUsage?.prompt_tokens ?? 0),
      providerUsage?.total_tokens ?? 0,
    ),
    estimated_cost_usd: estimateCostUsd(
      providerUsage?.prompt_tokens ?? 0,
      Math.max(completion, providerUsage?.completion_tokens ?? 0),
      model,
      provider,
    ),
    source: providerUsage?.source === 'provider' ? 'provider' : 'estimate',
    model,
    provider,
  }
}


