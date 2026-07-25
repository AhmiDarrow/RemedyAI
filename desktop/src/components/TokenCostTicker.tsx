import { useEffect, useState } from 'react'
import type { UsageSnapshot } from '../utils/tokenCost'
import { formatCost, formatTokens } from '../utils/tokenCost'

const HIDE_KEY = 'remedy.tokenTicker.hidden'

interface TokenCostTickerProps {
  /** Live run usage (current stream). */
  run: UsageSnapshot | null
  /** Session totals (sum of assistant message tokens + costs). */
  session: UsageSnapshot | null
  streaming?: boolean
  model?: string
  provider?: string
}

/**
 * Subtle, hideable live ticker for tokens + estimated API cost.
 * Preference persists in localStorage.
 */
export function TokenCostTicker({
  run,
  session,
  streaming = false,
  model,
  provider,
}: TokenCostTickerProps) {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(HIDE_KEY) === '1'
    } catch {
      return false
    }
  })
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(HIDE_KEY, hidden ? '1' : '0')
    } catch {
      /* */
    }
  }, [hidden])

  if (hidden) {
    return (
      <button
        type="button"
        onClick={() => setHidden(false)}
        className="fixed bottom-14 right-3 z-30 text-[10px] px-2 py-1 rounded-full opacity-70 hover:opacity-100 transition-opacity"
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
          color: 'var(--text-muted)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
        }}
        title="Show token & cost tracker"
      >
        $ tokens
      </button>
    )
  }

  const runTok = run?.total_tokens ?? 0
  const runCost = run?.estimated_cost_usd ?? 0
  const sessTok = session?.total_tokens ?? 0
  const sessCost = session?.estimated_cost_usd ?? 0
  const src = run?.source === 'provider' ? 'API' : 'est.'
  const hasData = runTok > 0 || sessTok > 0 || streaming

  return (
    <div
      className="fixed bottom-14 right-3 z-30 max-w-[min(20rem,calc(100vw-1.5rem))]"
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
        color: 'var(--text-secondary)',
        fontSize: 11,
      }}
    >
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <button
          type="button"
          className="flex-1 text-left min-w-0"
          onClick={() => setExpanded((e) => !e)}
          title="Token & cost details"
        >
          <div className="flex items-center gap-1.5 flex-wrap">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
              style={{
                background: streaming ? 'var(--accent)' : 'var(--text-muted)',
                opacity: streaming ? 1 : 0.5,
                animation: streaming ? 'pulse 1.2s ease infinite' : undefined,
              }}
            />
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
              {streaming ? 'Run' : 'Usage'}
            </span>
            {hasData ? (
              <>
                <span>{formatTokens(streaming ? runTok : sessTok || runTok)} tok</span>
                <span style={{ color: 'var(--text-muted)' }}>·</span>
                <span style={{ color: 'var(--accent)' }}>
                  {formatCost(streaming ? runCost : sessCost || runCost)}
                </span>
                {streaming && (
                  <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>{src}</span>
                )}
              </>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>idle</span>
            )}
          </div>
        </button>
        <button
          type="button"
          onClick={() => setHidden(true)}
          className="px-1 rounded shrink-0"
          style={{ color: 'var(--text-muted)' }}
          title="Hide tracker"
          aria-label="Hide token tracker"
        >
          ×
        </button>
      </div>
      {expanded && (
        <div
          className="px-2.5 pb-2 pt-0 space-y-1 border-t"
          style={{ borderColor: 'var(--border)', fontSize: 10 }}
        >
          <div className="flex justify-between gap-3 pt-1.5">
            <span style={{ color: 'var(--text-muted)' }}>This run</span>
            <span>
              in {formatTokens(run?.prompt_tokens ?? 0)} · out{' '}
              {formatTokens(run?.completion_tokens ?? 0)} · {formatCost(runCost)}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span style={{ color: 'var(--text-muted)' }}>Session</span>
            <span>
              {formatTokens(sessTok)} · {formatCost(sessCost)}
            </span>
          </div>
          {(model || provider) && (
            <div style={{ color: 'var(--text-muted)' }}>
              {[provider, model].filter(Boolean).join(' / ')}
              {run?.source === 'provider'
                ? ' · provider-reported'
                : ' · estimated (list prices)'}
            </div>
          )}
          <div style={{ color: 'var(--text-muted)', lineHeight: 1.35 }}>
            Hide anytime; re-open from the $ tokens chip. Estimates use published
            list prices — not a billing invoice.
          </div>
        </div>
      )}
    </div>
  )
}
