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
  clearLifeActivity,
  deleteLifeGoal,
  renameLifeGoal,
  type LifeBoard,
  type LifeGoal,
  type PendingApproval,
} from '../api/partner'
import { ApprovalBanner } from '../components/ApprovalBanner'
import { RemedyLogo } from '../components/RemedyLogo'
import { BrowserSlide } from '../components/slides/BrowserSlide'
import { GroveChat } from './GroveChat'
import { useSplit } from './useSplit'
import type { SendAttachment } from '../components/Composer'
import { messagesToMoments, latestExchange } from './storylineMoments'
import { getSettings } from '../api/settings'
import { patchVoiceSettings } from '../api/voice'
import { useVoice } from '../voice/useVoice'
import type { GenderRole } from '../voice/pickVoice'
import type { ChatMessage, ChatSession } from '../types'
import './grove.css'

const GOAL_SESSIONS_KEY = 'remedy.grove.goalSessions.v1'
const HOME_SESSION_KEY = 'remedy.grove.homeSession.v1'

function loadHomeSession(): string {
  try {
    return localStorage.getItem(HOME_SESSION_KEY) || ''
  } catch {
    return ''
  }
}

function saveHomeSession(id: string): void {
  try {
    localStorage.setItem(HOME_SESSION_KEY, id)
  } catch {
    /* */
  }
}

// Shown only when the board is empty — illustrations of what Grove is for,
// not real goals. Clicking one seeds a real goal with that title.
const EXAMPLE_PLOTS: { icon: string; title: string; hint: string }[] = [
  { icon: '🧾', title: 'Sort out this month’s receipts', hint: 'Drop a photo — I’ll total and organize them' },
  { icon: '✈️', title: 'Plan a weekend trip', hint: 'Tell me where and when; I’ll research and draft it' },
  { icon: '📄', title: 'Polish my resume', hint: 'Share it and the role — we’ll tailor it together' },
  { icon: '🛒', title: 'Reorder the usuals', hint: 'I can shop with you, you approve the checkout' },
]

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
    opts?: { focus?: boolean; origin?: string },
  ) => Promise<ChatSession | null>
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  messagesLoading: boolean
  handleSend: (text: string, attachments?: SendAttachment[]) => Promise<void> | void
  stop: () => void
  serverReady: boolean
  userName: string
  partnerName: string
  onSwitchToStudio: () => void
  /** Goal room Remedy asked to open herself (app_control open_goal). */
  openGoalId?: string | null
  /** Ack once the requested goal has been opened (or found absent). */
  onGoalOpened?: () => void
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
  openGoalId,
  onGoalOpened,
}: GroveAppProps) {
  const [view, setView] = useState<GroveView>({ kind: 'home' })
  const [tab, setTab] = useState<RoomTab>('alongside')
  const [board, setBoard] = useState<LifeBoard | null>(null)
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  // Draft lives inside GroveChat now; mic transcripts send directly.
  const [, setDraft] = useState('')
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
        const s = await createSession(`🌿 ${goal.title}`, undefined, { focus: true, origin: 'grove' })
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

  // Remedy opening a goal room "within herself" (app_control open_goal). Wait
  // for the board so a just-created goal resolves; ack once handled (found or
  // not) so App clears the request and we don't reopen on every board refresh.
  useEffect(() => {
    if (!openGoalId || !board) return
    const g = (board.goals || []).find((x) => x.id === openGoalId)
    if (g) openGoal(g)
    onGoalOpened?.()
  }, [openGoalId, board, openGoal, onGoalOpened])

  const sendFromGrove = useCallback(
    async (text: string, attachments?: SendAttachment[]) => {
      const t = text.trim()
      if ((!t && !attachments?.length) || busy) return
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
        await handleSend(t, attachments)
        setDraft('')
      } finally {
        setBusy(false)
      }
    },
    [busy, view, ensureGoalSession, handleSend],
  )
  sendFromGroveRef.current = sendFromGrove

  /** Session for attachment uploads from Grove home: reuse active, else create. */
  const homeSessionPendingRef = useRef<Promise<string | null> | null>(null)
  const homeSessionRef = useRef<string>(loadHomeSession())
  const ensureHomeSession = useCallback((): Promise<string | null> => {
    // Base Grove is her HOME — the whole PC, no project folder. Reuse the
    // home session only if it exists AND carries no project_path; never
    // inherit a Studio project session (project folders are Studio-only).
    const stored = homeSessionRef.current
    const storedSess = stored ? sessions.find((s) => s.id === stored) : undefined
    if (storedSess && !storedSess.project_path) {
      if (activeId !== storedSess.id) setActiveId(storedSess.id)
      return Promise.resolve(storedSess.id)
    }
    // The active session may already be a project-free one (not the stored
    // home) — only reuse it when it has no project folder.
    const cur = activeId ? sessions.find((s) => s.id === activeId) : undefined
    if (cur && !cur.project_path) {
      homeSessionRef.current = cur.id
      saveHomeSession(cur.id)
      return Promise.resolve(cur.id)
    }
    if (homeSessionPendingRef.current) return homeSessionPendingRef.current
    const p = (async () => {
      const s = await createSession(undefined, undefined, { focus: true, origin: 'grove' })
      if (s?.id) {
        homeSessionRef.current = s.id
        saveHomeSession(s.id)
        return s.id
      }
      return null
    })().finally(() => {
      homeSessionPendingRef.current = null
    })
    homeSessionPendingRef.current = p
    return p
  }, [activeId, sessions, setActiveId, createSession])

  // Base Grove home is the ongoing meeting place — one continuous, project-
  // free conversation. On entering home, bind to the home session (never a
  // Studio project chat). Her memory (Soul Field + Partner Memory) carries
  // the relationship across it, so it never feels like it has limits.
  useEffect(() => {
    if (view.kind !== 'home' || !serverReady) return
    // Delegate to ensureHomeSession: it reuses a stored/active project-free
    // session, and — crucially — PROVISIONS a fresh project-free home when the
    // only session around is a Studio project chat. Without provisioning, home
    // would render that Studio conversation until the first send (it never
    // should — Grove home has no project folder). The pending-ref guard inside
    // makes repeated calls (on every sessions/activeId change) idempotent.
    void ensureHomeSession()
  }, [view.kind, serverReady, ensureHomeSession])

  // Slide-able bars: the owner sizes their own windows; sizes persist.
  const homeSplit = useSplit({
    storageKey: 'remedy.grove.split.home.v1',
    axis: 'x',
    initial: 0.56,
    min: 0.28,
    max: 0.72,
    label: 'Resize plots and chat',
  })
  const roomSplit = useSplit({
    storageKey: 'remedy.grove.split.room.v1',
    axis: 'y',
    initial: 0.6,
    min: 0.3,
    max: 0.8,
    label: 'Resize stage and chat',
  })

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

  const removeGoal = useCallback(
    async (id: string) => {
      try {
        await deleteLifeGoal(id)
      } catch {
        /* best-effort; refresh reflects truth */
      }
      refreshBoard()
    },
    [refreshBoard],
  )

  const editGoal = useCallback(
    async (id: string, current: string) => {
      const next = window.prompt('Rename this plot', current)
      if (next == null) return
      const t = next.trim()
      if (!t || t === current) return
      try {
        await renameLifeGoal(id, t)
      } catch {
        /* best-effort */
      }
      refreshBoard()
    },
    [refreshBoard],
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
          {/* Circuit-R monogram — never the ✚ cross (non-medical branding). */}
          <div className="grove-brand">
            <RemedyLogo size={18} variant="auto" title="Remedy" />
            {partnerName || 'Remedy'}
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

        <div
          className={`grove-split${homeSplit.dragging ? ' dragging' : ''}`}
          ref={homeSplit.containerRef}
        >
        <div className="grove-scroll" style={{ width: `${homeSplit.ratio * 100}%` }}>
          <h1 className="grove-hello">
            {timeOfDayGreeting(userName)}
            <span>
              {goals.length
                ? `${goals.length} ${goals.length === 1 ? 'plot' : 'plots'} growing${needsYou ? ` · ${needsYou} thing${needsYou > 1 ? 's' : ''} need${needsYou > 1 ? '' : 's'} you` : ''}`
                : 'Nothing planted yet — tell me a goal and we’ll grow it together.'}
            </span>
          </h1>

          <div className="grove-away">
            {goals.length > 0 && lastStep?.did && (
              <div className="grove-pill">
                <span className="k" /> Last: {lastStep.did}
                {lastStep.goal ? ` — ${lastStep.goal}` : ''}
                <button
                  type="button"
                  className="grove-pill-x"
                  title="Clear this"
                  aria-label="Clear last activity"
                  onClick={() => {
                    void clearLifeActivity().then(refreshBoard).catch(() => {})
                  }}
                >
                  ×
                </button>
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
              <div
                key={g.id}
                className="grove-plot"
                role="button"
                tabIndex={0}
                onClick={() => openGoal(g)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    openGoal(g)
                  }
                }}
                data-testid="grove-plot"
              >
                <div className="grove-plot-actions">
                  <button
                    type="button"
                    className="grove-plot-act"
                    title="Rename"
                    aria-label={`Rename ${g.title}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      void editGoal(g.id, g.title)
                    }}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="grove-plot-act del"
                    title="Remove"
                    aria-label={`Remove ${g.title}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      void removeGoal(g.id)
                    }}
                  >
                    ×
                  </button>
                </div>
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
              </div>
            ))}

            {goals.length === 0 &&
              EXAMPLE_PLOTS.map((ex) => (
                <button
                  type="button"
                  key={ex.title}
                  className="grove-plot example"
                  onClick={() => void plantGoal(ex.title)}
                  title="Start this — I'll make it a real plot"
                >
                  <div className="kind">✨ example</div>
                  <h2>
                    {ex.icon} {ex.title}
                  </h2>
                  <div className="last">{ex.hint}</div>
                  <span className="livechip">Start this →</span>
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
                onClick={() => {
                  inputRef.current?.focus()
                  ;(
                    document.querySelector(
                      '.grove-chat input[aria-label="Talk to Remedy"]',
                    ) as HTMLInputElement | null
                  )?.focus()
                }}
              >
                Start in the chat →
              </button>
            </div>
          </div>
        </div>

        <div
          className={`grove-divider${homeSplit.dragging ? ' active' : ''}`}
          data-testid="grove-divider"
          {...homeSplit.dividerProps}
        >
          <span className="grip" />
        </div>

        <div className="grove-chatpane">
          {plantError && (
            <div className="grove-planterror" role="alert">
              {plantError}
            </div>
          )}
          <GroveChat
            messages={messages}
            partialText={partialText}
            streaming={streaming}
            loading={messagesLoading}
            userName={userName}
            partnerName={partnerName}
            serverReady={serverReady}
            placeholder="Tell me anything — a goal, an errand, a receipt, a bug…"
            onSend={sendFromGrove}
            onStop={stop}
            ensureSessionId={ensureHomeSession}
            sessionKey={activeId}
            onSpecialSend={async (t) => {
              if (/^i want to /i.test(t)) {
                return plantGoal(t.replace(/^i want to /i, ''))
              }
              return false
            }}
            micSupported={voice.micSupported}
            recording={voice.recording}
            onMic={() => void handleMic()}
          />
        </div>
        </div>
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
        <div
          className={`grove-stagewrap split${roomSplit.dragging ? ' dragging' : ''}`}
          ref={roomSplit.containerRef}
        >
          <div className="grove-stage" style={{ height: `${roomSplit.ratio * 100}%`, flex: 'none' }}>
            <BrowserSlide />
          </div>
          <div
            className={`grove-divider h${roomSplit.dragging ? ' active' : ''}`}
            data-testid="grove-room-divider"
            {...roomSplit.dividerProps}
          >
            <span className="grip" />
          </div>
          <div className="grove-chatpane room">
            {exchange.you || exchange.remedy || partialText ? null : (
              <div className="grove-room-hint">
                {messagesLoading
                  ? 'Opening this goal’s room…'
                  : 'Say what you’d like to do — I’ll drive, you watch.'}
              </div>
            )}
            <GroveChat
              messages={messages}
              partialText={partialText}
              streaming={streaming}
              loading={messagesLoading}
              userName={userName}
              partnerName={partnerName}
              serverReady={serverReady}
              placeholder={`Talk inside “${goal.title}” — attach anything…`}
              starters={[
                {
                  label: 'Where are we?',
                  text: 'Catch me up on this goal — where are we, and what’s next?',
                },
                {
                  label: 'Do the next step',
                  text: 'Take the next step on this goal now — I’ll watch.',
                },
                {
                  label: 'Add a note',
                  text: 'A note for this goal: ',
                },
              ]}
              onSend={sendFromGrove}
              onStop={stop}
              ensureSessionId={() => ensureGoalSession(goal)}
              sessionKey={activeId}
              micSupported={voice.micSupported}
              recording={voice.recording}
              onMic={() => void handleMic()}
            />
          </div>
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
