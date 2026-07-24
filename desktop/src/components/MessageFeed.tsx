import { useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'

export type ActiveTool = { name: string; status: 'running' | 'done' }

interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  loading: boolean
  planMode?: boolean
  /** Live tool activity while streaming (OpenCode-style cards). */
  activeTools?: ActiveTool[]
  /** Edit+resend: only for user messages (id + full text for the composer). */
  onEditUserMessage?: (msgId: string, content: string) => void
}

function MessageBubble({
  msg,
  partial,
  onEditUserMessage,
  streaming,
}: {
  msg: ChatMessage
  partial?: string
  onEditUserMessage?: (msgId: string, content: string) => void
  streaming?: boolean
}) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const text = msg.content + (partial || '')
  const showEdit =
    msg.role === 'user' && !msg.reverted && !!onEditUserMessage && !streaming

  const rowJustify = isSystem
    ? 'justify-center'
    : isUser
      ? 'justify-end'
      : 'justify-start'

  const bubbleBg = isUser
    ? 'var(--chat-user-bg)'
    : isSystem
      ? 'var(--chat-system-bg)'
      : 'var(--chat-assistant-bg)'
  const bubbleFg = isUser
    ? 'var(--chat-user-fg)'
    : isSystem
      ? 'var(--chat-system-fg)'
      : 'var(--chat-assistant-fg)'
  const bubbleBorder = isUser
    ? 'var(--chat-user-border)'
    : isSystem
      ? 'var(--chat-system-border)'
      : 'var(--chat-assistant-border)'

  const avatar = (
    <div
      className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold"
      style={{
        background: isUser
          ? 'var(--accent)'
          : isSystem
            ? 'var(--error)'
            : 'var(--bg-tertiary)',
        color: isUser ? '#fff' : 'var(--text-primary)',
        border: isUser ? 'none' : '1px solid var(--border)',
      }}
      aria-hidden
    >
      {isUser ? 'U' : isSystem ? '!' : 'R'}
    </div>
  )

  return (
    <div
      className={`group flex w-full px-4 py-1.5 ${rowJustify}`}
    >
      <div
        className={`relative flex gap-2 max-w-[min(var(--chat-max-width),100%)] ${
          isUser ? 'flex-row-reverse' : 'flex-row'
        }`}
      >
        {!isSystem && avatar}

        <div
          className="min-w-0 chat-bubble"
          style={{
            background: bubbleBg,
            color: bubbleFg,
            border: `1px solid ${bubbleBorder}`,
            borderRadius: 'var(--chat-bubble-radius)',
            padding: '0.65rem 0.85rem',
          }}
        >
          {!isUser && !isSystem && (
            <div
              className="text-[10px] font-semibold uppercase tracking-wide mb-1"
              style={{ color: 'var(--text-muted)' }}
            >
              Remedy
            </div>
          )}

          <div className="prose max-w-none text-sm message-body chat-bubble-body">
            {text ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre({ children }) {
                    return <pre>{children}</pre>
                  },
                  code({ children, className }) {
                    const inline = !className
                    if (inline) {
                      return (
                        <code
                          style={{
                            background: isUser
                              ? 'rgba(255,255,255,0.18)'
                              : 'var(--bg-tertiary)',
                            padding: '2px 6px',
                            borderRadius: 4,
                            fontSize: '0.9em',
                          }}
                        >
                          {children}
                        </code>
                      )
                    }
                    return <code className={className}>{children}</code>
                  },
                }}
              >
                {text}
              </ReactMarkdown>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>
                {msg.thinking
                  ? `Thinking: ${msg.thinking.slice(0, 100)}...`
                  : '(empty)'}
              </span>
            )}
          </div>

          {msg.tool_calls.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {msg.tool_calls.map((tc, i) => (
                <span
                  key={i}
                  className="text-xs px-2 py-0.5 rounded"
                  style={{
                    background: isUser ? 'rgba(255,255,255,0.15)' : 'var(--bg-tertiary)',
                    border: '1px solid var(--border)',
                    color: isUser ? bubbleFg : 'var(--text-secondary)',
                  }}
                >
                  {tc.name}
                </span>
              ))}
            </div>
          )}

          {showEdit && (
            <button
              type="button"
              onClick={() => onEditUserMessage?.(msg.id, msg.content ?? '')}
              className="absolute -top-2 -left-1 text-[10px] px-1.5 py-0.5 rounded opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus:opacity-100 focus:pointer-events-auto"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-muted)',
                border: '1px solid var(--border)',
                transition: 'opacity 0.12s ease',
              }}
              title="Edit and resend this message (removes later replies)"
            >
              Edit
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export function MessageFeed({
  messages,
  partialText,
  streaming,
  loading,
  planMode,
  activeTools = [],
  onEditUserMessage,
}: MessageFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  // Only auto-scroll when the user is already near the bottom (avoids fight/jitter).
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      stickToBottomRef.current = distance < 80
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (!stickToBottomRef.current) return
    // Instant while streaming tokens; smooth only when a full message lands.
    bottomRef.current?.scrollIntoView({
      behavior: streaming ? 'auto' : 'smooth',
      block: 'end',
    })
  }, [messages, partialText, streaming, activeTools])

  return (
    <div ref={scrollerRef} className="flex-1 overflow-y-auto message-feed py-2">
      {planMode && (
        <div
          className="mx-4 mt-2 mb-2 px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-2"
          style={{
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--accent)',
            color: 'var(--accent)',
          }}
        >
          <span>{'\u{1F9E0}'}</span>
          Plan mode active — describing approach; no tools will be executed
        </div>
      )}

      {loading && messages.length === 0 && (
        <div className="px-4 py-8 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading messages...
        </div>
      )}

      {messages.filter((m) => !m.reverted).map((msg) => (
        <MessageBubble
          key={msg.id}
          msg={msg}
          onEditUserMessage={onEditUserMessage}
          streaming={streaming}
        />
      ))}

      {streaming && activeTools.length > 0 && (
        <div className="px-4 py-2 flex flex-wrap gap-1.5 justify-start">
          {activeTools.map((t, i) => (
            <span
              key={`${t.name}-${i}`}
              className="text-xs px-2 py-1 rounded font-mono"
              style={{
                background: 'var(--bg-tertiary)',
                border: `1px solid ${t.status === 'running' ? 'var(--accent)' : 'var(--border)'}`,
                color: t.status === 'running' ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >
              {t.status === 'running' ? '⏳ ' : '✓ '}
              {t.name}
            </span>
          ))}
        </div>
      )}

      {streaming && partialText && (
        <MessageBubble
          msg={{
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
          }}
          partial={partialText}
        />
      )}

      {streaming && !partialText && activeTools.length === 0 && (
        <div className="flex justify-start px-4 py-2">
          <div
            className="text-sm px-3 py-2 rounded-2xl"
            style={{
              background: 'var(--chat-assistant-bg)',
              color: 'var(--text-muted)',
              border: '1px solid var(--chat-assistant-border)',
            }}
          >
            Thinking...
          </div>
        </div>
      )}

      {!loading && messages.length === 0 && !streaming && (
        <div
          className="flex flex-col items-center justify-center h-48 gap-2 px-6 text-center"
          style={{ color: 'var(--text-muted)' }}
        >
          <span style={{ color: 'var(--accent)', fontSize: '2rem' }}>{'\u2728'}</span>
          <div className="text-lg font-medium" style={{ color: 'var(--text-secondary)' }}>
            Your partner is ready
          </div>
          <div className="text-xs max-w-sm leading-relaxed">
            Ask anything, plan, research, or open a project to build.{' '}
            <code style={{ color: 'var(--accent)' }}>/help</code> lists commands and shortcuts.
          </div>
          <div className="text-xs max-w-sm leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            <code style={{ color: 'var(--accent)' }}>Enter</code> send ·{' '}
            <code style={{ color: 'var(--accent)' }}>Shift+Enter</code> new line ·{' '}
            <code style={{ color: 'var(--accent)' }}>@</code> reference files ·{' '}
            <code style={{ color: 'var(--accent)' }}>Ctrl+/</code> shortcuts
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
