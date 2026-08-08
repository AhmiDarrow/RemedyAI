import { useState, useEffect, useRef } from 'react'
import { healthCheck } from '../api/client'
import logoSrc from '/logo.png'

/** Keep splash short — server health is the real gate, not a marketing delay. */
const MIN_SPLASH_MS = 350
const FADE_MS = 180

/** Dark Forest splash palette (matches DEFAULT_THEME_ID — never flash purple Dark). */
const SPLASH_BG = '#0a0e0b'
const SPLASH_FG = '#e6ebe7'
const SPLASH_MUTED = '#9aa89e'
const SPLASH_ACCENT = '#4d7a5a'

interface SplashScreenProps {
  onReady: () => void
  onError: (msg: string) => void
}

export function SplashScreen({ onReady, onError }: SplashScreenProps) {
  const [status, setStatus] = useState<'starting' | 'connecting' | 'ready' | 'error'>(
    'starting',
  )
  const [dots, setDots] = useState('')
  const [fading, setFading] = useState(false)
  const startedAt = useRef(Date.now())
  // Stable callback refs — parent often passes inline arrows; putting those in
  // effect deps restarts the poll, cancels mid-handoff, and leaves "Ready" hung.
  const onReadyRef = useRef(onReady)
  const onErrorRef = useRef(onError)
  onReadyRef.current = onReady
  onErrorRef.current = onError

  useEffect(() => {
    // Remove HTML boot splash once React splash is up
    const boot = document.getElementById('boot-splash')
    if (boot) {
      boot.classList.add('boot-hidden')
      window.setTimeout(() => boot.remove(), FADE_MS)
    }
    // Force dark document chrome while splash is visible
    const html = document.documentElement
    const prevBg = html.style.background
    html.style.background = SPLASH_BG
    document.body.style.background = SPLASH_BG
    return () => {
      html.style.background = prevBg
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let done = false
    let attempts = 0
    startedAt.current = Date.now()

    async function finishReady() {
      if (done || cancelled) return
      done = true
      setStatus('ready')
      const elapsed = Date.now() - startedAt.current
      const wait = Math.max(0, MIN_SPLASH_MS - elapsed)
      await new Promise((r) => setTimeout(r, wait))
      if (cancelled) return
      setFading(true)
      await new Promise((r) => setTimeout(r, FADE_MS))
      if (cancelled) return
      try {
        onReadyRef.current()
      } catch (e) {
        console.error('Splash onReady failed:', e)
        // Still try to leave splash so the user is not stuck forever.
        onErrorRef.current(
          e instanceof Error ? e.message : 'Failed to enter app after server ready',
        )
      }
    }

    async function poll() {
      while (!cancelled && !done) {
        attempts++
        setStatus(attempts <= 1 ? 'starting' : 'connecting')
        let ok = false
        try {
          ok = await healthCheck(2000)
        } catch {
          ok = false
        }
        if (cancelled) return
        if (ok) {
          // Pre-warm local API token before the main app loads settings.
          try {
            const { clearApiToken, ensureApiToken } = await import('../api/client')
            clearApiToken()
            await ensureApiToken()
          } catch {
            /* token optional until settings fetch */
          }
          await finishReady()
          return
        }
        // ~90s total: first-run skill seed + auth can delay /api/status
        if (attempts >= 45) {
          setStatus('error')
          onErrorRef.current(
            'Server failed to start after ~90s. On a fresh install, wait a moment and Retry.',
          )
          return
        }
        const backoff = Math.min(250 * Math.pow(2, Math.min(attempts - 1, 3)), 2000)
        await new Promise((r) => setTimeout(r, backoff))
      }
    }

    void poll()
    return () => {
      cancelled = true
    }
    // Intentionally empty deps: one poll lifecycle per mount; callbacks via refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const timer = setInterval(() => {
      setDots((d) => (d.length >= 3 ? '' : d + '.'))
    }, 400)
    return () => clearInterval(timer)
  }, [])

  return (
    <div
      className="flex flex-col items-center justify-center h-full gap-5"
      style={{
        background: `radial-gradient(80% 55% at 50% 20%, ${SPLASH_ACCENT}22 0%, ${SPLASH_BG} 55%)`,
        color: SPLASH_FG,
        opacity: fading ? 0 : 1,
        transition: `opacity ${FADE_MS}ms ease`,
      }}
    >
      <img
        src={logoSrc}
        alt="Remedy"
        className="w-[240px] h-auto"
        draggable={false}
        style={{
          imageRendering: 'auto',
          objectFit: 'contain',
          animation: 'splash-in 0.5s ease both',
        }}
      />
      <div
        className="text-sm tracking-wide font-medium"
        style={{ color: status === 'error' ? '#f87171' : SPLASH_MUTED }}
      >
        {status === 'starting' && `Starting Remedy${dots}`}
        {status === 'connecting' && `Connecting to local server${dots}`}
        {status === 'ready' && `Ready${dots}`}
        {status === 'error' && 'Server connection failed. Is Remedy installed?'}
      </div>
      {status !== 'ready' && status !== 'error' && (
        <div
          className="w-36 h-1 rounded-full overflow-hidden mt-1"
          style={{ background: `${SPLASH_ACCENT}33` }}
          aria-hidden
        >
          <div
            className="h-full rounded-full"
            style={{
              width: '40%',
              background: SPLASH_ACCENT,
              animation: 'splash-bar 1.1s ease-in-out infinite',
            }}
          />
        </div>
      )}
      <style>{`
        @keyframes splash-in {
          from { opacity: 0; transform: scale(0.96); }
          to { opacity: 1; transform: scale(1); }
        }
        @keyframes splash-bar {
          0% { transform: translateX(-120%); }
          100% { transform: translateX(280%); }
        }
      `}</style>
    </div>
  )
}
