/** Client-side token helpers. No prices: Remedy has no price list, and a guess would read as a bill. */

export type UsageSnapshot = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  estimated_cost_usd: number
  /**
   * Prompt input the provider served from its cache. A build that keeps its
   * prompt cache warm costs a fraction of one that does not, so this is worth
   * showing next to the raw token counts rather than folding into them.
   */
  cache_read_tokens?: number
  /** Prompt input written into the provider cache this run. */
  cache_write_tokens?: number
  source?: 'provider' | 'estimate' | string
  model?: string | null
  provider?: string | null
}

/**
 * Cache line for the status area — empty string when the provider reported no
 * cache activity, so nothing is drawn for providers that do not cache.
 */
export function formatCacheUsage(usage: UsageSnapshot | null | undefined): string {
  const read = Math.max(0, Math.round(usage?.cache_read_tokens || 0))
  const write = Math.max(0, Math.round(usage?.cache_write_tokens || 0))
  if (!read && !write) return ''
  const parts: string[] = []
  if (read) parts.push(`${formatTokens(read)} cached`)
  if (write) parts.push(`${formatTokens(write)} written`)
  return parts.join(' · ')
}

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
    estimated_cost_usd: 0,
    // Cache counters are provider truth or nothing — never estimated.
    cache_read_tokens: providerUsage?.cache_read_tokens,
    cache_write_tokens: providerUsage?.cache_write_tokens,
    source: providerUsage?.source === 'provider' ? 'provider' : 'estimate',
    model,
    provider,
  }
}


