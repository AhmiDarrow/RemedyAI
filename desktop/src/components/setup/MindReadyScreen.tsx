import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getVisionStatus,
  installVision,
  startVisionServer,
  type VisionStatus,
} from '../../api/vision'
import {
  getSetupSession,
  retryMindSetup,
  mindFailureMessage,
  type SetupMindFailure,
  type SetupSession,
} from '../../api/setup'

interface MindReadyScreenProps {
  onReady: (session: SetupSession) => void
  onTraditional: (session?: SetupSession, reason?: string) => void
}

function progressPct(status: VisionStatus | null): number | null {
  const p = status?.progress
  if (!p || !p.bytes_total) return null
  const done = Number(p.bytes_done || 0)
  const total = Number(p.bytes_total || 0)
  if (total <= 0) return null
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)))
}

export function MindReadyScreen({ onReady, onTraditional }: MindReadyScreenProps) {
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const onReadyRef = useRef(onReady)
  onReadyRef.current = onReady

  useEffect(() => {
    let cancelled = false
    let finished = false
    let timer: ReturnType<typeof setInterval> | null = null

    const finish = async (latest: SetupSession | null) => {
      if (finished || cancelled) return
      finished = true
      if (timer) { clearInterval(timer); timer = null }
      onReadyRef.current(latest || ({ mode: 'conversation', status: 'pending', draft: {} } as SetupSession))
    }

    const tick = async () => {
      if (finished || cancelled) return
      try {
        const vis = await getVisionStatus({ full: true })
        if (cancelled || finished) return
        setStatus(vis)
        const setup = await getSetupSession().catch(() => null)
        const failure = setup?.mind_failure as SetupMindFailure | null | undefined
        const phase = String(vis.progress?.phase || '')
        const downloading = ['downloading', 'extracting', 'verifying', 'starting'].includes(phase)
        const ready = Boolean(vis.installed && (vis.ready || vis.running) && !vis.progress?.error && !downloading)
        if (ready) {
          if (!vis.running && !vis.ready) {
            setStarting(true)
            await startVisionServer().catch(() => {})
            setStarting(false)
          }
          const latest = setup || (await getSetupSession().catch(() => null))
          await finish(latest)
          return
        }
        if ((failure || vis.progress?.error) && !downloading) {
          setError(mindFailureMessage(failure) || String(vis.progress?.error || ''))
        }
        if (!vis.installed && !downloading && !vis.progress?.error) {
          await installVision().catch((e: unknown) => {
            if (!cancelled && !finished) setError(e instanceof Error ? e.message : String(e))
          })
        }
      } catch (e: unknown) {
        if (!cancelled && !finished) setError(e instanceof Error ? e.message : String(e))
      }
    }

    void tick()
    timer = setInterval(() => { void tick() }, 1500)
    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
  }, [])

  const pct = progressPct(status)
  const bytesLabel = useMemo(() => {
    const p = status?.progress
    if (!p?.bytes_total) return ''
    const gb = (n: number) => `${(n / (1024 ** 3)).toFixed(1)} GB`
    const done = gb(Number(p.bytes_done || 0))
    const total = gb(Number(p.bytes_total || 0))
    return `${done} / ${total}`
  }, [status])

  const phase = String(status?.progress?.phase || (starting ? 'starting' : 'preparing'))
  const phaseLabel: Record<string, string> = {
    preparing: 'Getting everything ready…',
    downloading: 'Downloading Remedy’s mind…',
    extracting: 'Unpacking the mind…',
    verifying: 'Verifying the download…',
    starting: 'Starting the mind…',
    ready: 'The mind is ready.',
  }

  return (
    <div className="remedy-shell setup-wizard-root">
      <div className="remedy-shell-card setup-wizard-card" style={{ maxWidth: 520 }}>
        <div className="px-7 pt-7 pb-3 text-center">
          <img src="/logo.png" alt="Remedy" className="remedy-shell-logo" draggable={false} />
          <h1 className="remedy-shell-title">Getting Remedy’s mind ready</h1>
          <p className="remedy-shell-subtitle">
            This is the last step of install. When her mind is ready, she’ll
            take it from here — a short conversation to set house rules.
            You can switch to Traditional setup at any time.
          </p>
        </div>
        <div className="px-7 pb-7 pt-2 space-y-5">
          <div>
            <div className="remedy-shell-progress-track" aria-label="Mind download progress">
              <div
                className={
                  'remedy-shell-progress-fill' + (pct == null ? ' is-unknown' : '')
                }
                style={{ width: `${pct ?? 40}%` }}
              />
            </div>
            <p className="mt-3 text-sm text-center" style={{ color: 'var(--text-secondary)' }}>
              {phaseLabel[phase] || status?.progress?.message || 'Working…'}
              {bytesLabel ? ` · ${bytesLabel}` : ''}
              {pct != null ? ` · ${pct}%` : ''}
            </p>
          </div>
          {error ? (
            <div
              className="rounded-lg px-3 py-3 text-sm"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              role="alert"
            >
              <p className="font-medium" style={{ color: 'var(--error)' }}>
                The mind couldn’t start
              </p>
              <p className="mt-1" style={{ color: 'var(--text-secondary)' }}>
                {error} We’ll open Traditional setup so you can keep going.
                You can retry the mind anytime.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="ui-btn ui-btn-primary"
                  onClick={() => {
                    setError('')
                    void retryMindSetup().catch(() => {})
                    void installVision().catch(() => {})
                  }}
                >
                  Retry
                </button>
                <button
                  type="button"
                  className="ui-btn ui-btn-ghost"
                  onClick={() => onTraditional(undefined, error)}
                >
                  Traditional setup
                </button>
              </div>
            </div>
          ) : (
            <div className="remedy-shell-actions flex-col">
              <button
                type="button"
                className="ui-btn ui-btn-ghost w-full"
                onClick={() => onTraditional()}
              >
                Traditional setup
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
