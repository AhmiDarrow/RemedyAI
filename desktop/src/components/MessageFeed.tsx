import { useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'

interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  loading: boolean
}

function MessageBubble({ msg, partial }: { msg: ChatMessage; partial?: string }) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'

  return (
    <div
      className="flex gap-3 px-4 py-3"
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
        <div className="prose prose-invert max-w-none text-sm" style={{ color: 'var(--text-primary)' }}>
          {msg.content || partial ? (
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
              {msg.content + (partial || '')}
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
      </div>
    </div>
  )
}

export function MessageFeed({ messages, partialText, streaming, loading }: MessageFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, partialText])

  return (
    <div className="flex-1 overflow-y-auto">
      {loading && messages.length === 0 && (
        <div className="px-4 py-8 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading messages...
        </div>
      )}

      {messages.map((msg) => (
        <MessageBubble key={msg.id} msg={msg} />
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
        <div className="px-4 py-12 text-center" style={{ color: 'var(--text-muted)' }}>
          <div className="text-lg mb-2">Remedy AI Desktop</div>
          <div>Type a message below to start.</div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
