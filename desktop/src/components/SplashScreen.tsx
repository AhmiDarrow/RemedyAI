import { useState, useEffect } from 'react'
import { healthCheck } from '../api/client'
import logoSrc from '/logo.png'

interface SplashScreenProps {
  onReady: () => void
  onError: (msg: string) => void
}

export function SplashScreen({ onReady, onError }: SplashScreenProps) {
  const [status, setStatus] = useState<'starting' | 'connecting' | 'ready' | 'error'>('starting')
  const [dots, setDots] = useState('')

  useEffect(() => {
    let cancelled = false
    let attempts = 0

    async function poll() {
      while (!cancelled) {
        attempts++
        setStatus('connecting')
        const ok = await healthCheck(2000)
        if (cancelled) return
        if (ok) {
          setStatus('ready')
          await new Promise((r) => setTimeout(r, 300))
          onReady()
          return
        }
        if (attempts >= 15) {
          setStatus('error')
          onError('Server failed to start after 30s')
          return
        }
        const backoff = Math.min(250 * Math.pow(2, attempts <= 1 ? 0 : attempts - 1), 2000)
        await new Promise((r) => setTimeout(r, backoff))
      }
    }

    poll()
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
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
    >
      <img
        src={logoSrc}
        alt="Remedy"
        className="w-[256px] h-auto"
        style={{ imageRendering: 'pixelated' }}
      />
      <div
        className="text-sm tracking-wide"
        style={{ color: 'var(--text-secondary)' }}
      >
        {status === 'starting' && 'Starting server...'}
        {status === 'connecting' && `Connecting${dots}`}
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
    </div>
  )
}
