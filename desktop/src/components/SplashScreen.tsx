import { useState, useEffect, useRef } from 'react'
import { healthCheck } from '../api/client'
import logoSrc from '/logo.png'

const MIN_SPLASH_MS = 3000
const FADE_MS = 350

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
  const finished = useRef(false)

  useEffect(() => {
    // Remove HTML boot splash once React splash is up
    const boot = document.getElementById('boot-splash')
    if (boot) {
      boot.classList.add('boot-hidden')
      window.setTimeout(() => boot.remove(), FADE_MS)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let attempts = 0

    async function finishReady() {
      if (finished.current || cancelled) return
      finished.current = true
      setStatus('ready')
      const elapsed = Date.now() - startedAt.current
      const wait = Math.max(0, MIN_SPLASH_MS - elapsed)
      await new Promise((r) => setTimeout(r, wait))
      if (cancelled) return
      setFading(true)
      await new Promise((r) => setTimeout(r, FADE_MS))
      if (cancelled) return
      onReady()
    }

    async function poll() {
      while (!cancelled && !finished.current) {
        attempts++
        setStatus(attempts <= 1 ? 'starting' : 'connecting')
        const ok = await healthCheck(2000)
        if (cancelled) return
        if (ok) {
          await finishReady()
          return
        }
        if (attempts >= 20) {
          setStatus('error')
          onError('Server failed to start after ~40s')
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
  }, [onReady, onError])

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
        background: 'var(--bg-primary)',
        color: 'var(--text-primary)',
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
      <div className="text-sm tracking-wide" style={{ color: 'var(--text-secondary)' }}>
        {status === 'starting' && `Starting Remedy${dots}`}
        {status === 'connecting' && `Connecting to local server${dots}`}
        {status === 'ready' && `Ready${dots}`}
        {status === 'error' && (
          <span style={{ color: 'var(--error)' }}>
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
                background: 'var(--accent)',
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
