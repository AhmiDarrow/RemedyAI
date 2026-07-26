/**
 * Remedy circuit-R monogram (not the wordmark).
 * Uses true-alpha public icons so chat/session avatars composite cleanly.
 */

import { useEffect, useMemo, useState } from 'react'

interface RemedyLogoProps {
  size?: number
  className?: string
  /** Soft rounded tile behind the icon */
  framed?: boolean
  title?: string
  /**
   * `alpha` — transparent monogram (chat bubbles).
   * `plate` — dark rounded plate (high contrast).
   * `auto` — theme-aware mono strokes on transparent.
   */
  variant?: 'alpha' | 'plate' | 'auto'
}

/** Cache-bust so updates replace WebView-cached icons. */
const ASSET_V = '0.14.9'

function isDarkTheme(): boolean {
  if (typeof document === 'undefined') return true
  const root = document.documentElement
  const attr = root.getAttribute('data-theme') || root.dataset.theme || ''
  if (attr === 'light') return false
  if (attr === 'dark') return true
  // Fallback: CSS color-scheme / prefers-color-scheme
  try {
    return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? true
  } catch {
    return true
  }
}

function pickSrc(variant: 'alpha' | 'plate' | 'auto', dark: boolean): string {
  if (variant === 'plate') return `/icon-plate.png?v=${ASSET_V}`
  if (variant === 'alpha') return `/icon.png?v=${ASSET_V}`
  // auto: light strokes on dark UI, dark strokes on light UI
  return dark
    ? `/icon-mono-light.png?v=${ASSET_V}`
    : `/icon-mono-dark.png?v=${ASSET_V}`
}

export function RemedyLogo({
  size = 28,
  className = '',
  framed = false,
  title = 'Remedy',
  variant = 'auto',
}: RemedyLogoProps) {
  const [dark, setDark] = useState(isDarkTheme)

  useEffect(() => {
    const sync = () => setDark(isDarkTheme())
    sync()
    const obs = new MutationObserver(sync)
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    })
    let mql: MediaQueryList | null = null
    try {
      mql = window.matchMedia('(prefers-color-scheme: dark)')
      mql.addEventListener?.('change', sync)
    } catch {
      /* */
    }
    return () => {
      obs.disconnect()
      mql?.removeEventListener?.('change', sync)
    }
  }, [])

  const src = useMemo(() => pickSrc(variant, dark), [variant, dark])

  const img = (
    <img
      src={src}
      alt={title}
      width={size}
      height={size}
      draggable={false}
      className={className}
      style={{
        width: size,
        height: size,
        objectFit: 'contain',
        display: 'block',
        background: 'transparent',
      }}
      onError={(e) => {
        const el = e.currentTarget
        // Fall through: mono → color alpha → favicon
        if (el.src.includes('icon-mono')) {
          el.src = `/icon.png?v=${ASSET_V}`
        } else if (el.src.includes('icon-plate')) {
          el.src = `/icon.png?v=${ASSET_V}`
        } else if (!el.src.includes('favicon.png')) {
          el.src = `/favicon.png?v=${ASSET_V}`
        }
      }}
    />
  )

  if (!framed) return img

  return (
    <div
      className="flex items-center justify-center flex-shrink-0 rounded-2xl overflow-hidden"
      style={{
        width: size + 16,
        height: size + 16,
        background:
          'linear-gradient(145deg, color-mix(in srgb, var(--accent) 22%, var(--bg-tertiary)), var(--bg-tertiary))',
        border: '1px solid var(--border)',
        boxShadow: '0 8px 24px color-mix(in srgb, var(--accent) 28%, transparent)',
      }}
      aria-hidden
    >
      {img}
    </div>
  )
}
