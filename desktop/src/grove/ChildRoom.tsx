/** A side job's room — a window onto one of Remedy's children.
 *
 * Transparent but not a second partner: the owner sees everything the child
 * has done and is doing (live drafts and tool steps land as it works) and can
 * stop it. Words typed here go to Remedy, named for this job, and she answers
 * in a floating bubble — the same exchange stays in her main thread. */

import { useCallback, useEffect, useState } from 'react'
import { listMessages } from '../api/messages'
import { needsOwner, stateWords, stopChild, type HeadChild } from '../api/head'
import { MessageFeed } from '../components/MessageFeed'
import type { ChatMessage } from '../types'
import type { ToolProcessMode } from '../utils/toolLabels'
import { RemedyBubble } from './RemedyBubble'
import { aboutChild, bubbleSlice, childPollMs } from './headHelpers'
import './grove-head.css'

export interface ChildRoomProps {
  child: HeadChild
  partnerName: string
  userName: string
  toolProcessMode?: ToolProcessMode
  /** Remedy's own conversation (what the bubble shows). */
  headMessages: ChatMessage[]
  headPartial: string
  headStreaming: boolean
  sendToRemedy: (text: string) => Promise<void>
  onChanged: () => void
}

export function ChildRoom({
  child,
  partnerName,
  userName,
  toolProcessMode = 'medium',
  headMessages,
  headPartial,
  headStreaming,
  sendToRemedy,
  onChanged,
}: ChildRoomProps) {
  const name = partnerName || 'Remedy'
  const [msgs, setMsgs] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [bubbleFrom, setBubbleFrom] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)

  const load = useCallback(() => {
    listMessages(child.session_id, 80)
      .then((m) => setMsgs(m))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [child.session_id])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const t = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      load()
    }, childPollMs(child.state))
    return () => clearInterval(t)
  }, [load, child.state])

  const send = async () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    // Anchor the bubble just after Remedy's latest message so earlier history
    // stays in her main thread, not in this little window.
    if (bubbleFrom === null) setBubbleFrom(headMessages.length ? headMessages[headMessages.length - 1].id : '')
    await sendToRemedy(aboutChild(child.label, text))
  }

  // The bubble shows Remedy's thread from just after the anchor, so earlier
  // history stays in her main thread, not in this little window.
  const bubbleMsgs = bubbleFrom === null ? [] : bubbleSlice(headMessages, bubbleFrom)

  const working = child.state === 'working' || child.state === 'queued'
  return (
    <div className="child-room" data-testid="grove-child-room">
      <div className={`child-head s-${child.state}`}>
        <span className="child-sprout" aria-hidden>
          🌱
        </span>
        <div className="child-headtext">
          <div className="child-label">
            {child.label}
            <span className={`child-state${needsOwner(child.state) ? ' needs' : ''}`}>{stateWords(child.state)}</span>
          </div>
          <div className="child-task" title={child.task}>
            {name}’s side job · {child.task}
          </div>
        </div>
        {working && (
          <button
            type="button"
            className="rh-btn danger"
            disabled={stopping}
            onClick={() => {
              setStopping(true)
              void stopChild(child.child_id)
                .catch(() => {})
                .finally(() => {
                  setStopping(false)
                  onChanged()
                  load()
                })
            }}
          >
            ■ Stop
          </button>
        )}
      </div>

      <div className="child-feed" aria-live="polite">
        <MessageFeed
          messages={msgs}
          partialText=""
          streaming={false}
          loading={loading}
          toolProcessMode={toolProcessMode}
          userName={name}
          partnerName={`🌱 ${child.label}`}
          sessionId={child.session_id}
          stickNonce={msgs.length}
          emptySub={working ? 'Starting up…' : 'Nothing here yet.'}
        />
        {working && (
          <div className="child-working" role="status">
            <span className="grove-live-dot" /> Working — new steps appear here as they happen
          </div>
        )}
      </div>

      <form
        className="child-talk"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={`Tell ${name} about this job — she’ll answer right here`}
          aria-label={`Message ${name} about ${child.label}`}
        />
        <button type="submit" className="rh-btn" disabled={!draft.trim()}>
          Send
        </button>
      </form>

      {bubbleFrom !== null && (
        <RemedyBubble
          name={name}
          userName={userName}
          messages={bubbleMsgs}
          partial={headStreaming ? headPartial : ''}
          streaming={headStreaming}
          onSend={(t) => sendToRemedy(aboutChild(child.label, t))}
          onClose={() => setBubbleFrom(null)}
        />
      )}
    </div>
  )
}
