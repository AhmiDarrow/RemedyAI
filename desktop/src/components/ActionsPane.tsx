import { forwardRef, useImperativeHandle, useMemo, useRef, useState } from 'react'
import type { ChatMessage } from '../types'
import type { BuildTodo } from './BuildTodos'
import { BuildTodos } from './BuildTodos'
import { WorkStrip } from './WorkStrip'
import { useStickToBottom } from '../hooks/useStickToBottom'
import type { ProcessStep, ToolProcessMode } from '../utils/toolLabels'
import {
  attachLiveWork,
  promptExcerpt,
  workTurnsFromMessages,
  type WorkTurn,
} from '../utils/workTurns'

export type ActionsPaneHandle = {
  scrollToTurn: (turnId: string) => void
}

export type ActionsPaneProps = {
  messages: ChatMessage[]
  partialThinking?: string
  processSteps?: ProcessStep[]
  buildTodos?: BuildTodo[]
  streaming: boolean
  toolProcessMode?: ToolProcessMode
  sessionId?: string | null
  lastUserId?: string | null
}

export const ActionsPane = forwardRef<ActionsPaneHandle, ActionsPaneProps>(
  function ActionsPane(
    {
      messages,
      partialThinking = '',
      processSteps = [],
      buildTodos = [],
      streaming,
      toolProcessMode = 'off',
      sessionId = null,
      lastUserId = null,
    },
    ref,
  ) {
    const turns = useMemo(() => {
      const base = workTurnsFromMessages(messages)
      return attachLiveWork(base, {
        streaming,
        thinking: partialThinking,
        steps: processSteps,
        todos: buildTodos,
        lastUserId,
      })
    }, [messages, streaming, partialThinking, processSteps, buildTodos, lastUserId])

    const processSig = processSteps
      .map(
        (s) =>
          `${s.id}:${s.status}:${(s.resultText || '').length}:${(s.argsText || '').length}`,
      )
      .join('|')
    const last = turns[turns.length - 1]
    const {
      setScroller,
      setContent,
      showJump,
      jumpLatest,
    } = useStickToBottom({
      followActive: streaming,
      startAtBottom: streaming,
      alwaysOfferJump: turns.length > 2 || streaming,
      reattachKey: `${lastUserId ?? ''}:${streaming ? 'live' : 'idle'}`,
      deps: [
        turns.length,
        last?.thinking.length,
        last?.steps.length,
        processSig,
        partialThinking,
        streaming,
      ],
    })

    const rowRefs = useRef<Record<string, HTMLElement | null>>({})
    const [focusId, setFocusId] = useState<string | null>(null)
    const focusTimer = useRef<number | null>(null)

    useImperativeHandle(ref, () => ({
      scrollToTurn(turnId: string) {
        const el = rowRefs.current[turnId]
        if (!el) return
        el.scrollIntoView({ block: 'start', behavior: 'smooth' })
        setFocusId(turnId)
        if (focusTimer.current != null) window.clearTimeout(focusTimer.current)
        focusTimer.current = window.setTimeout(() => setFocusId(null), 1600)
      },
    }))

    return (
      <div className="actions-pane-body work-host" data-testid="actions-pane">
        <div
          ref={setScroller}
          className="actions-pane-scroll"
          data-testid="actions-scroll"
        >
          <div ref={setContent} className="actions-pane-content">
            {turns.length === 0 && (
              <div className="actions-empty">
                <div className="actions-empty-title">No work yet</div>
                <div className="actions-empty-sub">
                  Thinking, tool calls, and the build checklist for each turn will land
                  here — the chat on the other side stays just the conversation.
                </div>
              </div>
            )}
            {turns.map((turn, i) => (
              <ActionTurn
                key={turn.id}
                turn={turn}
                latest={i === turns.length - 1}
                focused={focusId === turn.id}
                mode={toolProcessMode}
                sessionId={sessionId}
                rowRef={(el) => {
                  rowRefs.current[turn.id] = el
                  if (turn.userMessageId) rowRefs.current[turn.userMessageId] = el
                  for (const id of turn.assistantIds) rowRefs.current[id] = el
                }}
              />
            ))}
          </div>
        </div>
        {showJump && (
          <div className="scroll-latest-dock actions-jump">
            <button
              type="button"
              className="scroll-latest-fab scroll-latest-pill"
              onClick={jumpLatest}
              title="Jump to latest work"
            >
              <span>Latest work</span>
              <span aria-hidden>↓</span>
            </button>
          </div>
        )}
      </div>
    )
  },
)

function ActionTurn({
  turn,
  latest,
  focused,
  mode,
  sessionId,
  rowRef,
}: {
  turn: WorkTurn
  latest: boolean
  focused: boolean
  mode: ToolProcessMode
  sessionId: string | null
  rowRef: (el: HTMLElement | null) => void
}) {
  const excerpt = promptExcerpt(turn.prompt)
  const defaultOpen = Boolean(turn.live) || latest
  return (
    <article
      ref={rowRef}
      className={`action-turn${turn.live ? ' is-live' : ''}${focused ? ' is-focus' : ''}`}
      data-testid="action-turn"
      data-turn-id={turn.id}
      id={`action-turn-${turn.id}`}
    >
      <header className="action-turn-label">
        <span className="action-turn-kicker">
          {turn.live ? 'This turn' : 'Turn'}
        </span>
        {excerpt ? (
          <span className="action-turn-prompt" title={turn.prompt}>
            {excerpt}
          </span>
        ) : (
          <span className="action-turn-prompt is-muted">Working…</span>
        )}
      </header>
      <WorkStrip
        steps={turn.steps}
        thinking={turn.thinking}
        live={Boolean(turn.live)}
        mode={mode}
        defaultOpen={defaultOpen}
        defaultThinkingOpen={defaultOpen}
        sessionId={sessionId}
        extras={
          turn.todos?.length ? (
            <BuildTodos items={turn.todos} live={Boolean(turn.live)} defaultOpen={defaultOpen} />
          ) : undefined
        }
      />
    </article>
  )
}
