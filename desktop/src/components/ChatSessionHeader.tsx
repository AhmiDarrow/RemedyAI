/**
 * Slim header for the chat session column — title, model, mode chips.
 * Keeps the session window oriented without crowding the feed.
 */
import { memo } from 'react'

export type ChatSessionHeaderProps = {
  title: string
  partnerName?: string
  modelLabel?: string | null
  providerLabel?: string | null
  planMode?: boolean
  streaming?: boolean
  messageCount?: number
  onTogglePlanMode?: () => void
}

function shortModel(model: string | null | undefined): string {
  const m = (model || '').trim()
  if (!m) return ''
  if (m.length <= 28) return m
  return m.slice(0, 14) + '…' + m.slice(-10)
}

export const ChatSessionHeader = memo(function ChatSessionHeader({
  title,
  partnerName = 'Remedy',
  modelLabel,
  providerLabel,
  planMode = false,
  streaming = false,
  messageCount,
  onTogglePlanMode,
}: ChatSessionHeaderProps) {
  const displayTitle = (title || 'New chat').trim() || 'New chat'
  const model = shortModel(modelLabel)
  const provider = (providerLabel || '').trim()

  return (
    <header className="chat-session-header" aria-label="Current chat">
      <div className="chat-session-header-main min-w-0">
        <div className="chat-session-title truncate" title={displayTitle}>
          {displayTitle}
        </div>
        <div className="chat-session-sub truncate">
          <span className="chat-session-partner">{partnerName}</span>
          {provider || model ? (
            <>
              <span className="chat-session-dot" aria-hidden>
                ·
              </span>
              <span className="chat-session-model" title={[provider, modelLabel].filter(Boolean).join(' / ')}>
                {provider && model ? `${provider} / ${model}` : provider || model}
              </span>
            </>
          ) : null}
          {typeof messageCount === 'number' && messageCount > 0 ? (
            <>
              <span className="chat-session-dot" aria-hidden>
                ·
              </span>
              <span className="chat-session-count">
                {messageCount} msg{messageCount === 1 ? '' : 's'}
              </span>
            </>
          ) : null}
        </div>
      </div>

      <div className="chat-session-chips">
        {streaming && (
          <span className="chat-chip chat-chip-live" title="Reply in progress">
            <span className="live-stream-dot" aria-hidden />
            Live
          </span>
        )}
        <button
          type="button"
          className={`chat-chip chat-chip-mode ${planMode ? 'is-plan' : 'is-build'}`}
          onClick={onTogglePlanMode}
          title={
            planMode
              ? 'Plan mode — click for Build (or Ctrl+B / Shift+Tab)'
              : 'Build mode — click for Plan (or Ctrl+B / Shift+Tab)'
          }
          disabled={!onTogglePlanMode}
        >
          {planMode ? 'Plan' : 'Build'}
        </button>
      </div>
    </header>
  )
})
