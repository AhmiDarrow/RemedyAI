import { memo, type ReactNode } from 'react'
import type { ChatMessage } from '../types'
import type { ProcessStep, ToolProcessMode } from '../utils/toolLabels'
import type { StripProgress } from '../utils/workTimeline'
import { WorkStrip } from './WorkStrip'

/**
 * The live turn, split by audience:
 *  - thinking, tool calls, progress, and build extras live in one compact
 *    Working strip, collapsed by default;
 *  - every line written to the user (including text before a tool call)
 *    renders as a normal assistant bubble, including while streaming.
 * textBreaks is accepted so stream callers can keep passing it; the bubble
 * uses the full partialText rather than splitting narration out.
 */
export interface LiveTurnViewProps {
  partialText: string
  textBreaks?: number[]
  partialThinking?: string
  processSteps: ProcessStep[]
  /** Renders the reply bubble (keeps MessageBubble wiring in the feed). */
  renderReply: (msg: ChatMessage, reply: string) => ReactNode
  progress?: StripProgress | null
  mode?: ToolProcessMode
  sessionId?: string | null
  /** Live todos for the open strip body. */
  extras?: ReactNode
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

export const LiveTurnView = memo(function LiveTurnView({
  partialText,
  partialThinking = '',
  processSteps,
  renderReply,
  progress = null,
  mode = 'off',
  sessionId = null,
  extras,
}: LiveTurnViewProps) {
  // All assistant prose belongs in the bubble, including text written before
  // a tool call. The Working strip is only thinking, tools, and extras (todos).
  const hasReply = partialText.trim().length > 0
  return (
    <div className="live-turn" data-testid="live-turn">
      <div className="work-strip-slot">
        <WorkStrip
          steps={processSteps}
          thinking={partialThinking}
          live
          replying={hasReply && !processSteps.some((s) => s.status === 'running')}
          progress={progress}
          mode={mode}
          defaultOpen={false}
          sessionId={sessionId}
          extras={extras}
        />
      </div>
      {hasReply && (
        <div className="live-reply" data-testid="live-reply">
          {renderReply(STREAMING_MSG, partialText)}
        </div>
      )}
    </div>
  )
})
