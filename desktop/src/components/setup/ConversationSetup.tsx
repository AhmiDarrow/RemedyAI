import { useCallback, useEffect, useRef, useState } from 'react'
import { createSession } from '../../api/sessions'
import { listMessages, streamMessage } from '../../api/messages'
import type { ChatMessage } from '../../types'
import {
  getSetupSession,
  updateSetupSession,
  type SetupQuestion,
  type SetupSession,
} from '../../api/setup'

interface ConversationSetupProps {
  session: SetupSession
  onSession: (next: SetupSession) => void
  onTraditional: () => void
  onComplete: () => void
  mindFailedReason?: string
}

interface Bubble {
  id: string
  role: 'assistant' | 'user' | 'system'
  text: string
}

const TOPIC_KEYS: Record<string, string[]> = {
  names: ['user_name', 'name', 'agent_gender'],
  language: ['ui_language'],
  tone: ['persona', 'sarcasm_mode'],
  provider: ['llm_provider', 'llm_model', 'llm_base_url', 'custom_llm_name'],
  workspace: ['project_path'],
  proactive: ['proactive_reach_out', 'drives_enabled'],
  privacy: ['privacy_mode', 'inner_audit'],
  tokens: ['always_send_images', 'vision_force_decode', 'muscle_route'],
  gpu: ['muscle_gpu_policy'],
  consent: ['approval_mode', 'trust_profile'],
  interop: ['interop_inbound', 'interop_auto_reply'],
  updates: ['update_check'],
  launch: ['launch_at_login'],
}

const KICKOFF =
  "Hi — I'm Remedy. Let's set a few house rules so I can live with you the way you like. Every question is skippable, and you can skip all of them and keep the defaults. You can also switch to Traditional setup anytime; I won't lose what you've already said. What should I call you, and what should you call me?"

export function ConversationSetup({
  session,
  onSession,
  onTraditional,
  onComplete,
  mindFailedReason,
}: ConversationSetupProps) {
  const [input, setInput] = useState('')
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sidRef = useRef(session.setup_session_id || '')
  const startedRef = useRef(false)
  const scroller = useRef<HTMLDivElement | null>(null)
  const questions: SetupQuestion[] = session.questions || []

  const refresh = useCallback(async () => {
    const next = await getSetupSession()
    onSession(next)
    return next
  }, [onSession])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [bubbles, busy])

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    let cancelled = false
    ;(async () => {
      try {
        await updateSetupSession({ action: 'begin', onboarding_pending: true, mode: 'conversation' })
        let sid = sidRef.current
        if (!sid) {
          const created = await createSession({ title: 'House rules', origin_channel: 'desktop' })
          sid = created.id
          sidRef.current = sid
          await updateSetupSession({ setup_session_id: sid })
        }
        if (cancelled) return
        const existing = await listMessages(sid, 50, 0).catch(() => [] as ChatMessage[])
        const prior = (existing || []).filter((m) => m.role === 'user' || m.role === 'assistant')
        if (prior.length > 0) {
          setBubbles(
            prior.map((m) => ({
              id: m.id || `${m.role}-${m.created_at || Math.random()}`,
              role: m.role === 'user' ? 'user' : 'assistant',
              text: String(m.content || ''),
            })),
          )
          return
        }
        // Remedy speaks first. The kickoff is shown immediately; the mind
        // continues the conversation on the first user reply (prompt inject
        // also coaches her on the first owner turn).
        setBubbles([{ id: 'kickoff', role: 'assistant', text: KICKOFF }])
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const sid = sidRef.current
    if (!sid) {
      setError('Setup chat is not ready yet.')
      return
    }
    setInput('')
    setError('')
    const userId = `u-${Date.now()}`
    const asstId = `a-${Date.now()}`
    setBubbles((b) => [...b, { id: userId, role: 'user', text: trimmed }, { id: asstId, role: 'assistant', text: '' }])
    setBusy(true)
    let wrote = ''
    streamMessage(
      sid,
      trimmed,
      {
        onToken: (tok) => {
          wrote += tok
          setBubbles((b) => b.map((x) => (x.id === asstId ? { ...x, text: wrote } : x)))
        },
        onDone: () => {
          setBusy(false)
          void refresh()
        },
        onError: (msg) => {
          setBusy(false)
          setError(msg || 'The mind could not answer. You can retry, or switch to Traditional setup.')
        },
      },
      { chatMode: true },
    )
  }

  const skipAll = async () => {
    setBusy(true)
    try {
      const next = await updateSetupSession({ action: 'skip_all' })
      onSession(next)
      onComplete()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    setBusy(true)
    try {
      const next = await updateSetupSession({ action: 'confirm' })
      onSession(next)
      onComplete()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const skipped = new Set(session.skipped || [])
  const draft = session.draft || {}

  return (
    <div className="remedy-shell setup-wizard-root setup-conversation">
      <div className="setup-conversation-frame">
        <header className="setup-conversation-header">
          <div>
            <h1 className="remedy-shell-title" style={{ fontSize: '1.35rem', margin: 0 }}>
              House rules with Remedy
            </h1>
            <p className="remedy-shell-subtitle" style={{ marginTop: 6 }}>
              A short, skippable conversation. Traditional setup is always right here.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 justify-end">
            <button type="button" className="ui-btn ui-btn-ghost" onClick={onTraditional} disabled={busy}>
              Traditional setup
            </button>
            <button type="button" className="ui-btn ui-btn-ghost" onClick={() => void skipAll()} disabled={busy}>
              Skip all, use defaults
            </button>
          </div>
        </header>

        {mindFailedReason ? (
          <div className="setup-banner" role="status">
            <p>
              <strong>The mind couldn’t start.</strong> {mindFailedReason}{' '}
              You can keep going in Traditional setup, and retry the mind anytime.
            </p>
          </div>
        ) : null}

        <div className="setup-conversation-body">
          <aside className="setup-topics" aria-label="House rules topics">
            <h2>Topics</h2>
            <ul>
              {questions.map((q) => {
                const keys = TOPIC_KEYS[q.id] || []
                const done = keys.some((k) => Object.prototype.hasOwnProperty.call(draft, k))
                const isSkip = skipped.has(q.id)
                return (
                  <li key={q.id} className={isSkip ? 'is-skipped' : done ? 'is-done' : ''}>
                    <span>{q.title}</span>
                    <em>{isSkip ? 'skipped' : done ? 'set' : 'open'}</em>
                  </li>
                )
              })}
            </ul>
          </aside>
          <section className="setup-chat">
            <div className="setup-chat-log" ref={scroller}>
              {bubbles.map((b) => (
                <div key={b.id} className={`setup-bubble is-${b.role}`}>
                  {b.text || (b.role === 'assistant' && busy ? '…' : '')}
                </div>
              ))}
            </div>
            {error ? (
              <p className="setup-error" role="alert">{error}</p>
            ) : null}
            <form
              className="setup-chat-form"
              onSubmit={(e) => {
                e.preventDefault()
                void send(input)
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Type a reply, or “skip” / “skip all”"
                disabled={busy}
                aria-label="Reply to Remedy"
              />
              <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !input.trim()}>
                Send
              </button>
            </form>
            <div className="flex flex-wrap gap-2 mt-3">
              <button type="button" className="ui-btn ui-btn-ghost" disabled={busy} onClick={() => void send('skip')}>
                Skip this one
              </button>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void confirm()}>
                Looks good — use these
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
