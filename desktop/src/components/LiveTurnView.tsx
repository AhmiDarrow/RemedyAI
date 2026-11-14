import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { ChatMessage } from '../types'
import type { ProcessStep } from '../utils/toolLabels'
import { activityLabel, splitLiveText, summarizeSteps } from '../utils/liveSegments'

/**
 * The live turn, split by audience:
 *  - the reply being written to the user is a normal, prominent chat bubble;
 *  - background work (thinking, interim narration, status, tools, progress)
 *    sits in a subdued, collapsible "Working…" section with a step timeline.
 * The activity section is open while there is no reply yet and folds itself
 * once reply text arrives; a click on its header always wins after that.
 */
export interface LiveTurnViewProps {
  partialText: string
  textBreaks?: number[]
  partialThinking?: string
  processSteps: ProcessStep[]
  /** Renders the reply bubble (keeps MessageBubble wiring in the feed). */
  renderReply: (msg: ChatMessage, reply: string) => ReactNode
  /** Progress, todos, and the process trace for the activity body. */
  activityExtras?: ReactNode
}

const STREAMING_MSG: ChatMessage = {
  id: 'streaming',
  role: 'assistant',
  content: '',
  thinking: null,
  tool_calls: [],
  tool_results: [],
  model: null,
  agent: null,
  tokens: null,
  created_at: '',
  reverted: false,
}

export function LiveTurnView({
  partialText,
  textBreaks = [],
  partialThinking = '',
  processSteps,
  renderReply,
  activityExtras,
}: LiveTurnViewProps) {
  const { narration, reply } = useMemo(
    () => splitLiveText(partialText, textBreaks),
    [partialText, textBreaks],
  )
  const summary = useMemo(() => summarizeSteps(processSteps), [processSteps])
  const hasReply = reply.trim().length > 0
  const working = !hasReply || summary.running > 0
  const [userToggled, setUserToggled] = useState<boolean | null>(null)
  const open = userToggled ?? !hasReply
  // A new reply segment (after another tool round) re-applies the default.
  const lastBreaks = useRef(textBreaks.length)
  useEffect(() => {
    if (textBreaks.length !== lastBreaks.current) {
      lastBreaks.current = textBreaks.length
      setUserToggled(null)
    }
  }, [textBreaks.length])

  const thinking = partialThinking.trim()
  const hasActivity =
    thinking.length > 0 || narration.length > 0 || processSteps.length > 0 || Boolean(activityExtras)

  return (
    <div className="live-turn" data-testid="live-turn">
      {(hasActivity || !hasReply) && (
        <section
          className={`live-activity${open ? ' is-open' : ''}`}
          data-testid="live-activity"
          aria-label="Background work"
        >
          <button
            type="button"
            className="live-activity-header"
            aria-expanded={open}
            onClick={() => setUserToggled(!open)}
          >
            {working && <span className="live-stream-dot" aria-hidden />}
            <span className="live-activity-title">{activityLabel(summary, working)}</span>
            <span className="live-activity-chevron" aria-hidden>
              {open ? '▾' : '▸'}
            </span>
          </button>
          {open && (
            <div className="live-activity-body">
              {thinking && (
                <div className="live-activity-thinking" data-testid="live-thinking">
                  {thinking}
                </div>
              )}
              {narration.length > 0 && (
                <ol className="live-activity-timeline" data-testid="live-narration">
                  {narration.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ol>
              )}
              {activityExtras}
            </div>
          )}
        </section>
      )}
      {hasReply && (
        <div className="live-reply" data-testid="live-reply">
          {renderReply(STREAMING_MSG, reply)}
        </div>
      )}
    </div>
  )
}
