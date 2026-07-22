import { useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'

interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  loading: boolean
  planMode?: boolean
  onRevert?: (msgId: string) => void
}

function MessageBubble({
  msg,
  partial,
  onRevert,
}: {
  msg: ChatMessage
  partial?: string
  onRevert?: (msgId: string) => void
}) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const text = msg.content + (partial || '')

  return (
    <div
      className="group flex gap-3 px-4 py-3"
      style={{
        background: isUser ? 'transparent' : 'var(--bg-secondary)',
      }}
    >
      <div
        className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
        style={{
          background: isUser
            ? 'var(--accent)'
            : isSystem
              ? 'var(--error)'
              : 'var(--bg-tertiary)',
          color: isUser ? '#fff' : 'var(--text-primary)',
        }}
      >
        {isUser ? 'U' : isSystem ? '!' : 'R'}
      </div>

      <div className="flex-1 min-w-0">
        <div className="prose prose-invert max-w-none text-sm">
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
                          background: 'var(--bg-tertiary)',
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
              {msg.thinking ? `Thinking: ${msg.thinking.slice(0, 100)}...` : '(empty)'}
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
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                {tc.name}
              </span>
            ))}
          </div>
        )}

        {msg.role === 'assistant' && !msg.reverted && onRevert && (
          <button
            onClick={() => onRevert(msg.id)}
            className="hidden group-hover:inline-block text-xs mt-1 px-1.5 py-0.5 rounded"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-muted)',
            }}
            title="Undo this message"
          >
            Undo
          </button>
        )}

        {msg.reverted && (
          <span
            className="text-xs italic mt-1"
            style={{ color: 'var(--text-muted)' }}
          >
            (reverted)
          </span>
        )}
      </div>
    </div>
  )
}

export function MessageFeed({ messages, partialText, streaming, loading, planMode, onRevert }: MessageFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, partialText])

  return (
    <div className="flex-1 overflow-y-auto">
      {planMode && (
        <div
          className="mx-4 mt-2 px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-2"
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

      {messages.map((msg) => (
        <MessageBubble key={msg.id} msg={msg} onRevert={onRevert} />
      ))}

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

      {streaming && !partialText && (
        <div className="px-4 py-3" style={{ color: 'var(--text-muted)' }}>
          Thinking...
        </div>
      )}

      {!loading && messages.length === 0 && !streaming && (
        <div className="flex flex-col items-center justify-center h-48 gap-3" style={{ color: 'var(--text-muted)' }}>
          <span style={{ color: 'var(--accent)', fontSize: '2rem' }}>{'\u2728'}</span>
          <div className="text-lg font-medium" style={{ color: 'var(--text-secondary)' }}>Remedy Desktop</div>
          <div className="text-xs">Type a message below to start, or use <code style={{ color: 'var(--accent)' }}>/help</code> for commands</div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Tip: type <code style={{ color: 'var(--accent)' }}>@</code> to reference files
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
