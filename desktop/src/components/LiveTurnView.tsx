import { memo, useMemo, type ReactNode } from 'react'
import type { ChatMessage } from '../types'
import type { ProcessStep, ToolProcessMode } from '../utils/toolLabels'
import { splitLiveText } from '../utils/liveSegments'
import type { StripProgress } from '../utils/workTimeline'
import { WorkStrip } from './WorkStrip'

/**
 * The live turn, split by audience:
 *  - background work (thinking, narration, tools, progress) is one compact,
 *    low-contrast Working strip with a single live status line;
 *  - the reply bubble appears only once real reply text is streaming, and
 *    never contains narration (text written before a tool call).
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
  textBreaks = [],
  partialThinking = '',
  processSteps,
  renderReply,
  progress = null,
  mode = 'off',
  sessionId = null,
  extras,
}: LiveTurnViewProps) {
  const { reply } = useMemo(() => splitLiveText(partialText, textBreaks), [partialText, textBreaks])
  const hasReply = reply.trim().length > 0
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
          defaultOpen={mode === 'full'}
          sessionId={sessionId}
          extras={extras}
        />
      </div>
      {hasReply && (
        <div className="live-reply" data-testid="live-reply">
          {renderReply(STREAMING_MSG, reply)}
        </div>
      )}
    </div>
  )
})
