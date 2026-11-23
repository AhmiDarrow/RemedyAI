import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import {
  CHAT_ACTIONS_WIDTH_DEFAULT,
  clampChatActionsWidth,
  loadChatSplit,
  saveChatSplit,
  shouldStackChatSplit,
  swapChatSplitSides,
  type ChatActionsSide,
  type ChatSplitPrefs,
} from '../workspace/chatSplit'
import { MessageFeed, type MessageFeedProps } from './MessageFeed'
import { ActionsPane, type ActionsPaneHandle } from './ActionsPane'

export type ChatWorkspaceProps = MessageFeedProps & {
  /** Controlled side; omit to use (and persist) saved prefs. */
  actionsSide?: ChatActionsSide
  actionsWidth?: number
  onActionsSideChange?: (side: ChatActionsSide) => void
  onActionsWidthChange?: (width: number) => void
  /** Persist to remedy.chatSplit.v1. Off in the visual harness. */
  persist?: boolean
}

function lastUserIdOf(messages: MessageFeedProps['messages']): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!
    if (m.role === 'user' && !m.reverted) return m.id
  }
  return null
}

export function ChatWorkspace({
  actionsSide: sideProp,
  actionsWidth: widthProp,
  onActionsSideChange,
  onActionsWidthChange,
  persist = true,
  ...feed
}: ChatWorkspaceProps) {
  const [prefs, setPrefs] = useState<ChatSplitPrefs>(() => {
    const stored = persist ? loadChatSplit() : { actionsSide: 'left' as const, actionsWidth: CHAT_ACTIONS_WIDTH_DEFAULT }
    return {
      actionsSide: sideProp ?? stored.actionsSide,
      actionsWidth: widthProp ?? stored.actionsWidth,
    }
  })
  useEffect(() => {
    if (sideProp) setPrefs((p) => ({ ...p, actionsSide: sideProp }))
  }, [sideProp])
  useEffect(() => {
    if (widthProp != null) setPrefs((p) => ({ ...p, actionsWidth: widthProp }))
  }, [widthProp])

  const commit = useCallback(
    (next: ChatSplitPrefs) => {
      setPrefs(next)
      if (persist) saveChatSplit(next)
      if (next.actionsSide !== prefs.actionsSide) onActionsSideChange?.(next.actionsSide)
      if (next.actionsWidth !== prefs.actionsWidth) onActionsWidthChange?.(next.actionsWidth)
    },
    [persist, prefs.actionsSide, prefs.actionsWidth, onActionsSideChange, onActionsWidthChange],
  )

  const side = sideProp ?? prefs.actionsSide
  const width = widthProp ?? prefs.actionsWidth

  const shellRef = useRef<HTMLDivElement | null>(null)
  const [narrow, setNarrow] = useState(false)
  const [mobilePane, setMobilePane] = useState<'chat' | 'actions'>('chat')
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const el = shellRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const apply = (w: number) => setNarrow(shouldStackChatSplit(w))
    apply(el.clientWidth)
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? el.clientWidth
      apply(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const onSwap = useCallback(() => {
    const next = swapChatSplitSides({ actionsSide: side, actionsWidth: width })
    commit(next)
  }, [side, width, commit])

  const onResizePointer = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      if (narrow || e.button !== 0) return
      e.preventDefault()
      const shell = shellRef.current
      if (!shell) return
      const startX = e.clientX
      const startW = clampChatActionsWidth(width, width, shell.clientWidth)
      const sign = side === 'left' ? 1 : -1
      const handle = e.currentTarget
      handle.setPointerCapture(e.pointerId)
      setDragging(true)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      const move = (ev: PointerEvent) => {
        const nextW = clampChatActionsWidth(
          startW + sign * (ev.clientX - startX),
          startW,
          shell.clientWidth,
        )
        setPrefs((p) => ({ ...p, actionsWidth: nextW }))
      }
      const up = (ev: PointerEvent) => {
        handle.releasePointerCapture(ev.pointerId)
        handle.removeEventListener('pointermove', move)
        handle.removeEventListener('pointerup', up)
        handle.removeEventListener('pointercancel', up)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        setDragging(false)
        setPrefs((p) => {
          const next = {
            ...p,
            actionsWidth: clampChatActionsWidth(p.actionsWidth, startW, shell.clientWidth),
          }
          if (persist) saveChatSplit(next)
          onActionsWidthChange?.(next.actionsWidth)
          return next
        })
      }
      handle.addEventListener('pointermove', move)
      handle.addEventListener('pointerup', up)
      handle.addEventListener('pointercancel', up)
    },
    [narrow, width, side, persist, onActionsWidthChange],
  )

  const actionsRef = useRef<ActionsPaneHandle | null>(null)
  const onSelectTurn = useCallback(
    (turnId: string) => {
      if (narrow) setMobilePane('actions')
      // Wait a frame so the pane is visible when we just switched.
      requestAnimationFrame(() => actionsRef.current?.scrollToTurn(turnId))
    },
    [narrow],
  )

  const lastUserId = lastUserIdOf(feed.messages)
  const live = Boolean(feed.streaming)

  const actions = (
    <section
      className="actions-pane"
      data-testid="actions-column"
      style={narrow ? undefined : { width, flex: '0 0 auto' }}
      aria-label="Coding and actions"
    >
      <header className="split-pane-head">
        <div className="split-pane-title">
          <span className="split-pane-kicker">Coding / Actions</span>
          <span className="split-pane-sub">Thinking, tools, and the build</span>
        </div>
        {!narrow && (
          <button
            type="button"
            className="chat-split-swap"
            data-testid="chat-split-swap"
            onClick={onSwap}
            title="Swap Actions and Chat"
            aria-label="Swap Actions and Chat"
          >
            ⇄
          </button>
        )}
      </header>
      <ActionsPane
        ref={actionsRef}
        messages={feed.messages}
        partialThinking={feed.partialThinking}
        processSteps={feed.processSteps}
        buildTodos={feed.buildTodos}
        streaming={feed.streaming}
        toolProcessMode={feed.toolProcessMode}
        sessionId={feed.sessionId}
        lastUserId={lastUserId}
      />
    </section>
  )

  const chat = (
    <section className="chat-pane" data-testid="chat-column" aria-label="Chat">
      <header className="split-pane-head">
        <div className="split-pane-title">
          <span className="split-pane-kicker">Chat</span>
          <span className="split-pane-sub">Messages to you</span>
        </div>
        {live && <span className="chat-working-chip" data-testid="chat-working-chip">Working</span>}
        {!narrow && (
          <button
            type="button"
            className="chat-split-swap"
            onClick={onSwap}
            title="Swap Actions and Chat"
            aria-label="Swap Actions and Chat"
          >
            ⇄
          </button>
        )}
      </header>
      <div className="chat-pane-feed">
        <MessageFeed {...feed} hideWork onSelectTurn={onSelectTurn} />
      </div>
    </section>
  )

  const handle = (
    <div
      className={`slide-resize-handle chat-split-handle${dragging ? ' is-active' : ''}`}
      data-testid="chat-split-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize actions pane"
      aria-hidden={narrow}
      onPointerDown={onResizePointer}
      onDoubleClick={() => {
        const next = {
          actionsSide: side,
          actionsWidth: clampChatActionsWidth(
            CHAT_ACTIONS_WIDTH_DEFAULT,
            CHAT_ACTIONS_WIDTH_DEFAULT,
            shellRef.current?.clientWidth,
          ),
        }
        commit(next)
      }}
    />
  )

  return (
    <div
      ref={shellRef}
      className={`chat-workspace${narrow ? ' is-narrow' : ''} is-actions-${side}`}
      data-testid="chat-workspace"
      data-actions-side={side}
      data-mobile-pane={narrow ? mobilePane : undefined}
    >
      {narrow && (
        <div className="chat-split-tabs" role="tablist" aria-label="Chat or actions">
          <button
            type="button"
            role="tab"
            aria-selected={mobilePane === 'actions'}
            className={`chat-split-tab${mobilePane === 'actions' ? ' is-active' : ''}`}
            data-testid="chat-split-tab-actions"
            onClick={() => setMobilePane('actions')}
          >
            {live && <span className="live-stream-dot" aria-hidden />}
            Actions
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mobilePane === 'chat'}
            className={`chat-split-tab${mobilePane === 'chat' ? ' is-active' : ''}`}
            data-testid="chat-split-tab-chat"
            onClick={() => setMobilePane('chat')}
          >
            Chat
          </button>
          <button
            type="button"
            className="chat-split-swap is-tab"
            data-testid="chat-split-swap"
            onClick={onSwap}
            title="Swap Actions and Chat when side by side"
            aria-label="Swap Actions and Chat"
          >
            ⇄
          </button>
        </div>
      )}
      {side === 'left' ? (
        <>
          {actions}
          {handle}
          {chat}
        </>
      ) : (
        <>
          {chat}
          {handle}
          {actions}
        </>
      )}
    </div>
  )
}
