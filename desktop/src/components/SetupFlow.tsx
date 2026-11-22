import { useCallback, useEffect, useState } from 'react'
import { SetupWizard } from './SetupWizard'
import { MindReadyScreen } from './setup/MindReadyScreen'
import { ConversationSetup } from './setup/ConversationSetup'
import {
  getSetupSession,
  updateSetupSession,
  mindFailureMessage,
  type SetupSession,
} from '../api/setup'

interface SetupFlowProps {
  open: boolean
  onComplete: () => void
  /** Opened from the one-time update pop-up (or a later "go over house rules again"). */
  revisit?: boolean
}

type Stage = 'mind' | 'conversation' | 'traditional'

export function SetupFlow({ open, onComplete, revisit = false }: SetupFlowProps) {
  const [session, setSession] = useState<SetupSession | null>(null)
  const [stage, setStage] = useState<Stage>('mind')
  const [failReason, setFailReason] = useState('')
  const [loaded, setLoaded] = useState(false)

  const applySession = useCallback((next: SetupSession, prefer?: Stage) => {
    setSession(next)
    if (prefer) {
      setStage(prefer)
      return
    }
    if (next.mode === 'traditional') {
      setStage('traditional')
      return
    }
    if (next.mind_ready) {
      setStage('conversation')
      return
    }
    if (next.mind_failure || next.fallback_suggested) {
      setFailReason(mindFailureMessage(next.mind_failure))
      setStage('traditional')
      return
    }
    setStage('mind')
  }, [])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      try {
        const next = await getSetupSession()
        if (cancelled) return
        const begun = revisit
          ? next
          : await updateSetupSession({ action: 'begin', onboarding_pending: true }).catch(() => next)
        if (revisit) {
          const prefer = begun.mind_ready
            ? 'conversation'
            : (begun.mind_failure || begun.fallback_suggested ? 'traditional' : 'mind')
          applySession(begun, prefer)
        } else {
          applySession(begun)
        }
      } catch {
        if (!cancelled) setStage('mind')
      } finally {
        if (!cancelled) setLoaded(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, applySession, revisit])

  const goTraditional = useCallback(
    async (from?: SetupSession, reason?: string) => {
      if (reason) setFailReason(reason)
      try {
        const next = await updateSetupSession({ mode: 'traditional' })
        applySession(next, 'traditional')
      } catch {
        if (from) applySession({ ...from, mode: 'traditional' }, 'traditional')
        else setStage('traditional')
      }
    },
    [applySession],
  )

  const goConversation = useCallback(async () => {
    try {
      const next = await updateSetupSession({ mode: 'conversation' })
      if (next.mind_ready) applySession(next, 'conversation')
      else applySession(next, 'mind')
    } catch {
      setStage(session?.mind_ready ? 'conversation' : 'mind')
    }
  }, [applySession, session])

  if (!open) return null
  if (!loaded && !session) {
    return (
      <div className="remedy-shell setup-wizard-root">
        <div className="remedy-shell-card" style={{ padding: '2rem', textAlign: 'center' }}>
          Loading setup…
        </div>
      </div>
    )
  }

  if (stage === 'mind') {
    return (
      <MindReadyScreen
        onReady={(next) => applySession(next, 'conversation')}
        onTraditional={(next, reason) => {
          void goTraditional(next, reason)
        }}
      />
    )
  }

  if (stage === 'conversation') {
    return (
      <ConversationSetup
        session={session || { mode: 'conversation', status: 'pending', draft: {}, questions: [] }}
        onSession={setSession}
        onTraditional={() => { void goTraditional() }}
        onComplete={onComplete}
        mindFailedReason={failReason}
      />
    )
  }

  return (
    <SetupWizard
      open={open}
      onComplete={onComplete}
      mindFailedReason={failReason}
      onSwitchToConversation={() => { void goConversation() }}
    />
  )
}
