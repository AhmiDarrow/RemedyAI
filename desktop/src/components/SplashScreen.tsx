import { useState, useEffect, useRef } from 'react'
import { healthCheck } from '../api/client'
import logoSrc from '/logo.png'

const MIN_SPLASH_MS = 3000
const FADE_MS = 350

/** Always-dark splash palette (never follow light system theme). */
const SPLASH_BG = '#0a0a1a'
const SPLASH_FG = '#e8e8f0'
const SPLASH_MUTED = '#9a9ab0'
const SPLASH_ACCENT = '#6c8cff'

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
          await finishReady()
          return
        }
        if (attempts >= 20) {
          setStatus('error')
          onErrorRef.current('Server failed to start after ~40s')
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
      className="flex flex-col items-center justify-center h-full gap-6"
      style={{
        background: SPLASH_BG,
        color: SPLASH_FG,
        opacity: fading ? 0 : 1,
        transition: `opacity ${FADE_MS}ms ease`,
      }}
    >
      <img
        src={logoSrc}
        alt="Remedy"
        className="w-[256px] h-auto"
        style={{
          imageRendering: 'pixelated',
          animation: 'splash-in 0.5s ease both',
        }}
      />
      <div className="text-sm tracking-wide" style={{ color: SPLASH_MUTED }}>
        {status === 'starting' && `Starting Remedy${dots}`}
        {status === 'connecting' && `Connecting to local server${dots}`}
        {status === 'ready' && `Ready${dots}`}
        {status === 'error' && (
          <span style={{ color: '#f87171' }}>
            Server connection failed. Is Remedy installed?
          </span>
        )}
      </div>
      {status !== 'ready' && status !== 'error' && (
        <div className="flex gap-1 mt-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-2 h-2 rounded-full animate-pulse"
              style={{
                background: SPLASH_ACCENT,
                animationDelay: `${i * 150}ms`,
                opacity: 0.5 + i * 0.2,
              }}
            />
          ))}
        </div>
      )}
      <style>{`
        @keyframes splash-in {
          from { opacity: 0; transform: scale(0.96); }
          to { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  )
}
