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
import { COMPUTER_UI_EVENT } from '../api/computer'
import { RemedyLogo } from '../components/RemedyLogo'
import { BrowserSlide } from '../components/slides/BrowserSlide'
import { GroveChat } from './GroveChat'
import { useSplit } from './useSplit'
import type { SendAttachment } from '../components/Composer'
import { messagesToMoments, latestExchange } from './storylineMoments'
import { getSettings } from '../api/settings'
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
  handleSend: (
    text: string,
    attachments?: SendAttachment[],
    opts?: { mode?: 'after' | 'interrupt' | 'steer'; sessionId?: string },
  ) => Promise<void> | void
  stickNonce: number
  stop: () => void
  serverReady: boolean
  userName: string
  partnerName: string
  onSwitchToStudio: () => void
  /** Mirror of this surface's playback state for the shared status bar. */
  onSpeakingChange?: (speaking: boolean) => void
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
  stickNonce,
  stop,
  serverReady,
  userName,
  partnerName,
  onSwitchToStudio,
  onSpeakingChange,
  openGoalId,
  onGoalOpened,
}: GroveAppProps) {
  const [view, setView] = useState<GroveView>({ kind: 'home' })
  const [tab, setTab] = useState<RoomTab>('alongside')
  // Home surface mirrors a goal room's tabs: plots (default) · Alongside
  // (live browser stage — shop/browse together) · Storyline (the record).
  const [homeTab, setHomeTab] = useState<'plots' | 'alongside' | 'storyline'>('plots')

  // When Remedy starts driving the Browser rail (shopping, forms), bring the
  // stage on screen: an unmounted rail has no bounds, so her page snapshots
  // degrade to desktop captures and she loses her eyes. Alongside = watching.
  const viewKindRef = useRef(view.kind)
  viewKindRef.current = view.kind
  useEffect(() => {
    const onComputerUi = (e: Event) => {
      const detail = (e as CustomEvent).detail as { openBrowser?: boolean } | undefined
      if (!detail?.openBrowser) return
      if (viewKindRef.current === 'home') {
        setHomeTab((cur) => (cur === 'alongside' ? cur : 'alongside'))
      } else {
        setTab((cur) => (cur === 'alongside' ? cur : 'alongside'))
      }
    }
    window.addEventListener(COMPUTER_UI_EVENT, onComputerUi)
    return () => window.removeEventListener(COMPUTER_UI_EVENT, onComputerUi)
  }, [])
  const [board, setBoard] = useState<LifeBoard | null>(null)
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  // Draft lives inside GroveChat now; mic transcripts send directly.
  const [, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [micErr, setMicErr] = useState('')
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

  useEffect(() => {
    onSpeakingChange?.(voice.speaking)
  }, [voice.speaking, onSpeakingChange])

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
    if (voice.transcribing) return
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
      setMicErr('')
      const ok = await voice.startRecording()
      if (!ok) {
        setMicErr('Microphone is blocked in this browser. Allow it for this site, or type instead.')
      }
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

  const sendFromGrove = useCallback(
    async (text: string, attachments?: SendAttachment[]) => {
      const t = text.trim()
      if ((!t && !attachments?.length) || busy) return
      setBusy(true)
      try {
        let sid: string | null = null
        if (view.kind === 'goal') {
          sid = await ensureGoalSession(view.goal)
        } else {
          sid = await ensureHomeSession()
        }
        // Grove has no queue UI: a message sent while she is working steers
        // her — the words join the running turn at its next step (she keeps
        // going and folds them in). With an attachment, or if the turn just
        // ended, it falls back to stop-and-send. Idle sends are unaffected.
        await handleSend(t, attachments, {
          ...(sid ? { sessionId: sid } : {}),
          mode: 'steer',
        })
        setDraft('')
      } finally {
        setBusy(false)
      }
    },
    [busy, view, ensureGoalSession, ensureHomeSession, handleSend],
  )
  sendFromGroveRef.current = sendFromGrove

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
    if (el && (tab === 'storyline' || homeTab === 'storyline'))
      el.scrollTop = el.scrollHeight
  }, [moments.length, partialText, streaming, tab, homeTab])

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
          <div className="grove-tabs home">
            <button
              type="button"
              className={homeTab === 'plots' ? 'on' : ''}
              onClick={() => setHomeTab('plots')}
            >
              Home
            </button>
            <button
              type="button"
              className={homeTab === 'alongside' ? 'on' : ''}
              onClick={() => setHomeTab('alongside')}
              title="Live stage — I drive the browser, you watch (shop, forms, email)"
            >
              Alongside
            </button>
            <button
              type="button"
              className={homeTab === 'storyline' ? 'on' : ''}
              onClick={() => setHomeTab('storyline')}
              title="Everything we say and do, in order, in plain words"
            >
              📖 Storyline
            </button>
          </div>
          <button
            type="button"
            className="grove-switch"
            onClick={onSwitchToStudio}
            title="Full workbench: files, terminal, raw tools"
          >
            Grove ✦ · switch to <b>Studio</b>
          </button>
        </div>

        {homeTab === 'plots' && (
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
          {micErr ? (
            <div className="grove-planterror" role="alert">
              {micErr}
            </div>
          ) : null}
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
            stickNonce={stickNonce}
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
        )}
        {homeTab === 'alongside' && (
          <div
            className={`grove-stagewrap split${roomSplit.dragging ? ' dragging' : ''}`}
            ref={roomSplit.containerRef}
          >
            <div
              className="grove-stage"
              style={{ height: `${roomSplit.ratio * 100}%`, flex: 'none' }}
            >
              <BrowserSlide />
            </div>
            <div
              className={`grove-divider h${roomSplit.dragging ? ' active' : ''}`}
              {...roomSplit.dividerProps}
            >
              <span className="grip" />
            </div>
            <div className="grove-chatpane room">
              <GroveChat
                messages={messages}
                partialText={partialText}
                streaming={streaming}
                loading={messagesLoading}
                userName={userName}
                partnerName={partnerName}
                serverReady={serverReady}
                placeholder="Tell me what to do — I’ll drive, you watch."
                onSend={sendFromGrove}
                onStop={stop}
                ensureSessionId={ensureHomeSession}
                sessionKey={activeId}
                stickNonce={stickNonce}
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
        )}
        {homeTab === 'storyline' && (
          <div className="grove-story" data-testid="grove-home-storyline" ref={storyRef}>
            {moments.length === 0 && !messagesLoading && (
              <div className="grove-story-empty">
                Everything we say and everything I do lands here, in order, in
                plain words.
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
            {micErr ? (
              <div className="grove-planterror" role="alert">
                {micErr}
              </div>
            ) : null}
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
              stickNonce={stickNonce}
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
