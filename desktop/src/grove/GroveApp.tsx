/** Grove — the partner surface and default home (docs/LIFE_TASK_PARTNER.md).
 *
 * Home is the owner's life, not a chat log: goals as living plots Remedy
 * tends. Opening a plot is a room with two tabs — Alongside (the live stage:
 * Browser rail + talk strip) and Storyline (the goal's co-written record).
 * Studio (the full workbench) is one tap away; the choice persists.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  getLifeBoard,
  listApprovals,
  createLifeGoal,
  type LifeBoard,
  type LifeGoal,
  type PendingApproval,
} from '../api/partner'
import { ApprovalBanner } from '../components/ApprovalBanner'
import { BrowserSlide } from '../components/slides/BrowserSlide'
import { messagesToMoments, latestExchange } from './storylineMoments'
import { getSettings } from '../api/settings'
import { patchVoiceSettings } from '../api/voice'
import { useVoice } from '../voice/useVoice'
import type { GenderRole } from '../voice/pickVoice'
import type { ChatMessage, ChatSession } from '../types'
import './grove.css'

const GOAL_SESSIONS_KEY = 'remedy.grove.goalSessions.v1'

function loadGoalSessions(): Record<string, string> {
  try {
    const raw = localStorage.getItem(GOAL_SESSIONS_KEY)
    const obj = raw ? JSON.parse(raw) : null
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

function saveGoalSessions(map: Record<string, string>): void {
  try {
    localStorage.setItem(GOAL_SESSIONS_KEY, JSON.stringify(map))
  } catch {
    /* */
  }
}

function timeOfDayGreeting(name: string): string {
  const h = new Date().getHours()
  const part = h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
  return name ? `${part}, ${name}` : part
}

export interface GroveAppProps {
  sessions: ChatSession[]
  activeId: string | null
  setActiveId: (id: string) => void
  createSession: (
    title?: string,
    llm?: { provider?: string; model?: string },
    opts?: { focus?: boolean },
  ) => Promise<ChatSession | null>
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  messagesLoading: boolean
  handleSend: (text: string) => Promise<void> | void
  stop: () => void
  serverReady: boolean
  userName: string
  partnerName: string
  onSwitchToStudio: () => void
}

type GroveView = { kind: 'home' } | { kind: 'goal'; goal: LifeGoal }
type RoomTab = 'alongside' | 'storyline'

export function GroveApp({
  sessions,
  activeId,
  setActiveId,
  createSession,
  messages,
  partialText,
  streaming,
  messagesLoading,
  handleSend,
  stop,
  serverReady,
  userName,
  partnerName,
  onSwitchToStudio,
}: GroveAppProps) {
  const [view, setView] = useState<GroveView>({ kind: 'home' })
  const [tab, setTab] = useState<RoomTab>('alongside')
  const [board, setBoard] = useState<LifeBoard | null>(null)
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const goalSessionsRef = useRef<Record<string, string>>(loadGoalSessions())
  const inputRef = useRef<HTMLInputElement | null>(null)

  // ---- voice: speak-back (gender-matched) + hearing ----
  const [gender, setGender] = useState<GenderRole>('female')
  useEffect(() => {
    getSettings()
      .then((s) => {
        const g = String(s.agent_gender || 'female').toLowerCase()
        setGender(g === 'male' ? 'male' : g === 'neutral' ? 'neutral' : 'female')
      })
      .catch(() => {})
  }, [])
  const voice = useVoice({ gender, enabled: serverReady })
  const speakReplies = voice.status?.settings?.speak_replies ?? false
  const toggleSpeakReplies = useCallback(() => {
    const next = !speakReplies
    if (!next) voice.stopSpeaking()
    patchVoiceSettings({ speak_replies: next })
      .then(() => voice.refreshStatus())
      .catch(() => {})
  }, [speakReplies, voice])

  // Speak each finished reply aloud when the owner chose speak-back.
  const prevStreamingRef = useRef(false)
  useEffect(() => {
    if (prevStreamingRef.current && !streaming && speakReplies) {
      for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i]
        if (m.role === 'assistant' && (m.content || '').trim() && !m.reverted) {
          void voice.speak(m.content)
          break
        }
      }
    }
    prevStreamingRef.current = streaming
  }, [streaming, speakReplies, messages, voice])

  const sendFromGroveRef = useRef<((t: string) => Promise<void>) | null>(null)
  const handleMic = useCallback(async () => {
    if (voice.recording) {
      const text = await voice.stopRecording()
      if (text) {
        setDraft(text)
        // Hands-free: heard speech sends (payment/credential steps still
        // stop at their non-waivable checkpoints server-side).
        await sendFromGroveRef.current?.(text)
      }
    } else {
      voice.stopSpeaking()
      await voice.startRecording()
    }
  }, [voice])

  const refreshBoard = useCallback(() => {
    if (!serverReady) return
    getLifeBoard()
      .then(setBoard)
      .catch(() => {})
    listApprovals()
      .then(setApprovals)
      .catch(() => {})
  }, [serverReady])

  useEffect(() => {
    refreshBoard()
    const t = setInterval(refreshBoard, 12_000)
    return () => clearInterval(t)
  }, [refreshBoard])

  /** Session bound to a goal's room — reuse if it still exists, else create.
   *
   * In-flight promises are de-duped per goal so a double-click (or mic +
   * re-click) can't create two sessions and orphan the first. */
  const goalPendingRef = useRef<Map<string, Promise<string | null>>>(new Map())
  const ensureGoalSession = useCallback(
    (goal: LifeGoal): Promise<string | null> => {
      const map = goalSessionsRef.current
      const existing = map[goal.id]
      if (existing && sessions.some((s) => s.id === existing)) {
        setActiveId(existing)
        return Promise.resolve(existing)
      }
      const inflight = goalPendingRef.current.get(goal.id)
      if (inflight) return inflight
      const p = (async () => {
        const s = await createSession(`🌿 ${goal.title}`, undefined, { focus: true })
        if (s?.id) {
          map[goal.id] = s.id
          saveGoalSessions(map)
          return s.id
        }
        return null
      })().finally(() => goalPendingRef.current.delete(goal.id))
      goalPendingRef.current.set(goal.id, p)
      return p
    },
    [sessions, setActiveId, createSession],
  )

  const openGoal = useCallback(
    (goal: LifeGoal) => {
      setView({ kind: 'goal', goal })
      setTab('alongside')
      void ensureGoalSession(goal)
    },
    [ensureGoalSession],
  )

  const sendFromGrove = useCallback(
    async (text: string) => {
      const t = text.trim()
      if (!t || busy) return
      setBusy(true)
      try {
        if (view.kind === 'goal') {
          // Make sure this goal's session exists + is active before sending,
          // so a fast type-and-Enter doesn't post into the previous room.
          await ensureGoalSession(view.goal)
        }
        // handleSend self-provisions a session when none is active (home
        // talkbar with no chat yet) and rebinds on activeId change — so we
        // never pre-create here (that made a stray second session).
        await handleSend(t)
        setDraft('')
      } finally {
        setBusy(false)
      }
    },
    [busy, view, ensureGoalSession, handleSend],
  )
  sendFromGroveRef.current = sendFromGrove

  const [plantError, setPlantError] = useState('')
  const plantGoal = useCallback(
    async (title: string): Promise<boolean> => {
      const t = title.trim()
      if (!t) return false
      setPlantError('')
      try {
        const g = await createLifeGoal(t)
        refreshBoard()
        if (g?.id) openGoal(g)
        return true
      } catch {
        setPlantError("I couldn't plant that goal just now — try again in a moment.")
        return false
      }
    },
    [refreshBoard, openGoal],
  )

  const goals = board?.goals || []
  const needsYou = approvals.length
  const lastStep = board?.last_step || null
  const exchange = useMemo(() => latestExchange(messages), [messages])
  const moments = useMemo(() => messagesToMoments(messages), [messages])

  // Storyline keeps the newest moment (and "Happening now") in view.
  const storyRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = storyRef.current
    if (el && tab === 'storyline') el.scrollTop = el.scrollHeight
  }, [moments.length, partialText, streaming, tab])

  // ---------------- home ----------------
  if (view.kind === 'home') {
    return (
      <div className="grove-surface" data-testid="grove-home">
        <div className="grove-topbar">
          <div className="grove-brand">✚ {partnerName || 'Remedy'}</div>
          <button
            type="button"
            className={`grove-voicetoggle${speakReplies ? ' on' : ''}${voice.speaking ? ' speaking' : ''}`}
            onClick={toggleSpeakReplies}
            aria-pressed={speakReplies}
            aria-label={speakReplies ? 'Speaking replies aloud' : 'Replies are silent'}
            title={
              speakReplies
                ? 'Speaking replies aloud — click to go quiet'
                : 'Click to have replies spoken aloud'
            }
          >
            {speakReplies ? '🔊 aloud' : '🔇 quiet'}
          </button>
          <button
            type="button"
            className="grove-switch"
            onClick={onSwitchToStudio}
            title="Full workbench: files, terminal, raw tools"
          >
            Grove ✦ · switch to <b>Studio</b>
          </button>
        </div>

        <div className="grove-scroll">
          <h1 className="grove-hello">
            {timeOfDayGreeting(userName)}
            <span>
              {goals.length
                ? `${goals.length} ${goals.length === 1 ? 'plot' : 'plots'} growing${needsYou ? ` · ${needsYou} thing${needsYou > 1 ? 's' : ''} need${needsYou > 1 ? '' : 's'} you` : ''}`
                : 'Nothing planted yet — tell me a goal and we’ll grow it together.'}
            </span>
          </h1>

          <div className="grove-away">
            {lastStep?.did && (
              <div className="grove-pill">
                <span className="k" /> Last: {lastStep.did}
                {lastStep.goal ? ` — ${lastStep.goal}` : ''}
              </div>
            )}
            {needsYou > 0 && (
              <div className="grove-pill needs">
                <span className="k" /> Needs you: {needsYou} decision
                {needsYou > 1 ? 's' : ''} waiting
              </div>
            )}
          </div>

          {needsYou > 0 && (
            <div className="grove-approvals">
              <ApprovalBanner sessionId={null} onResolved={refreshBoard} />
            </div>
          )}

          <div className="grove-plots">
            {goals.map((g) => (
              <button
                type="button"
                key={g.id}
                className="grove-plot"
                onClick={() => openGoal(g)}
                data-testid="grove-plot"
              >
                <div className="kind">
                  🌿 {g.horizon || g.status || 'growing'}
                </div>
                <h2>{g.title}</h2>
                {(g.next_action || g.done_looks_like) && (
                  <div className="last">
                    {g.next_action
                      ? `Next: ${g.next_action}`
                      : `Done looks like: ${g.done_looks_like}`}
                  </div>
                )}
                <span className="livechip">Sit with me →</span>
              </button>
            ))}
            <div className="grove-plot seed">
              <div>🌱</div>
              <div>
                Plant something new — <em>“I want to…”</em> is enough.
              </div>
              <button
                type="button"
                className="seedbtn"
                onClick={() => inputRef.current?.focus()}
              >
                Start below
              </button>
            </div>
          </div>
        </div>

        {plantError && (
          <div className="grove-planterror" role="alert">
            {plantError}
          </div>
        )}
        <form
          className="grove-talkbar"
          onSubmit={(e) => {
            e.preventDefault()
            const t = draft.trim()
            if (!t) return
            if (/^i want to /i.test(t)) {
              // Only clear the draft once the goal is actually planted.
              void plantGoal(t.replace(/^i want to /i, '')).then((ok) => {
                if (ok) setDraft('')
              })
            } else {
              void sendFromGrove(t)
            }
          }}
        >
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Talk to Remedy — a goal, an errand, or a question"
            placeholder={
              !serverReady
                ? 'Connecting…'
                : voice.recording
                  ? 'Listening — click the mic again when you’re done…'
                  : voice.transcribing
                    ? 'Heard you — writing it down…'
                    : 'Tell me anything — a goal, an errand, a worry…'
            }
            disabled={!serverReady}
          />
          {voice.micSupported && (
            <button
              type="button"
              className={`grove-micbtn${voice.recording ? ' rec' : ''}`}
              onClick={() => void handleMic()}
              disabled={!serverReady}
              aria-pressed={voice.recording}
              aria-label={
                voice.recording ? 'Stop and send what you said' : 'Speak instead of typing'
              }
              title={
                voice.recording
                  ? 'Stop and send what you said'
                  : 'Speak instead of typing (stays on this PC)'
              }
            >
              🎙
            </button>
          )}
          <button type="submit" className="grove-mic" title="Send" aria-label="Send">
            ↑
          </button>
        </form>
        {voice.recording && (
          <div className="grove-sr-live" role="status" aria-live="polite">
            Listening…
          </div>
        )}
      </div>
    )
  }

  // ---------------- goal room ----------------
  const goal = view.goal
  return (
    <div className="grove-surface" data-testid="grove-room">
      <div className="grove-topbar">
        <div className="grove-crumb">
          <button type="button" className="seg" onClick={() => setView({ kind: 'home' })}>
            Grove
          </button>
          <span className="sep">▸</span>
          <span className="seg here">{goal.title}</span>
          {streaming && <span className="grove-live-dot" title="Remedy is working" />}
        </div>
        <div className="grove-tabs">
          <button
            type="button"
            className={tab === 'alongside' ? 'on' : ''}
            onClick={() => setTab('alongside')}
          >
            Alongside
          </button>
          <button
            type="button"
            className={tab === 'storyline' ? 'on' : ''}
            onClick={() => setTab('storyline')}
          >
            📖 Storyline
          </button>
        </div>
        <button
          type="button"
          className={`grove-voicetoggle${speakReplies ? ' on' : ''}${voice.speaking ? ' speaking' : ''}`}
          onClick={toggleSpeakReplies}
          aria-pressed={speakReplies}
          aria-label={speakReplies ? 'Speaking replies aloud' : 'Replies are silent'}
          title={
            speakReplies
              ? 'Speaking replies aloud — click to go quiet'
              : 'Click to have replies spoken aloud'
          }
        >
          {speakReplies ? '🔊' : '🔇'}
        </button>
        <button type="button" className="grove-switch" onClick={onSwitchToStudio}>
          switch to <b>Studio</b>
        </button>
      </div>

      <div className="grove-approvals">
        <ApprovalBanner sessionId={activeId} onResolved={refreshBoard} />
      </div>

      {tab === 'alongside' ? (
        <div className="grove-stagewrap">
          <div className="grove-stage">
            <BrowserSlide />
          </div>
          <div className="grove-talkstrip">
            <div className="caps">
              {exchange.you && (
                <div className="cap you">You — “{exchange.you.slice(0, 160)}”</div>
              )}
              {(partialText || exchange.remedy) && (
                <div className="cap rem">
                  <b>{partnerName || 'Remedy'} —</b>{' '}
                  {(partialText || exchange.remedy || '').slice(0, 200)}
                  {streaming && <span className="grove-typing">…</span>}
                </div>
              )}
              {!exchange.you && !exchange.remedy && !partialText && (
                <div className="cap you">
                  {messagesLoading
                    ? 'Opening this goal’s room…'
                    : 'Say what you’d like to do — I’ll drive, you watch.'}
                </div>
              )}
            </div>
            {streaming && (
              <button type="button" className="grove-chip stop" onClick={() => stop()}>
                ⏸ Pause
              </button>
            )}
          </div>
          <form
            className="grove-talkbar room"
            onSubmit={(e) => {
              e.preventDefault()
              void sendFromGrove(draft)
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label={`Talk inside ${goal.title}`}
              placeholder={
                voice.recording
                  ? 'Listening — click the mic again when you’re done…'
                  : `Talk inside “${goal.title}”…`
              }
              disabled={!serverReady}
            />
            {voice.micSupported && (
              <button
                type="button"
                className={`grove-micbtn${voice.recording ? ' rec' : ''}`}
                onClick={() => void handleMic()}
                disabled={!serverReady}
                aria-pressed={voice.recording}
                aria-label={
                  voice.recording ? 'Stop and send what you said' : 'Speak instead of typing'
                }
                title={
                  voice.recording
                    ? 'Stop and send what you said'
                    : 'Speak instead of typing (stays on this PC)'
                }
              >
                🎙
              </button>
            )}
            <button type="submit" className="grove-mic" title="Send" aria-label="Send">
              ↑
            </button>
          </form>
          {voice.recording && (
            <div className="grove-sr-live" role="status" aria-live="polite">
              Listening…
            </div>
          )}
        </div>
      ) : (
        <div className="grove-story" data-testid="grove-storyline" ref={storyRef}>
          {moments.length === 0 && !messagesLoading && (
            <div className="grove-story-empty">
              The story starts when you do — everything we say and everything I
              do in this goal lands here, in order, in plain words.
            </div>
          )}
          {moments.map((mo) => (
            <div key={mo.id} className={`grove-mo ${mo.kind}`}>
              <div className="who">
                {mo.kind === 'you-said'
                  ? 'You said'
                  : mo.kind === 'remedy-said'
                    ? `${partnerName || 'Remedy'} said`
                    : `${partnerName || 'Remedy'} did`}
              </div>
              <div className="said">{mo.text}</div>
            </div>
          ))}
          {streaming && (
            <div className="grove-mo remedy-said now">
              <div className="who">Happening now</div>
              <div className="said">{partialText || '…'}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
