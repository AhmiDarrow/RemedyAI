import {
  useMemo,
  useState,
  useCallback,
  type ReactNode,
  Fragment,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'
import { sanitizeAssistantText } from '../utils/sanitizeChat'
import { dayKey, dayLabel } from '../utils/relativeTime'
import { TaskProgress, type TaskProgressInfo } from './TaskProgress'
import { ImageLightbox } from './ImageLightbox'
import { RemedyLogo } from './RemedyLogo'
import {
  IconBtn,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconCopy,
  IconEdit,
  IconRefresh,
} from './icons'
import { ProcessTrace } from './ProcessTrace'
import {
  stepsFromMessageTools,
  type ProcessStep,
  type ToolProcessMode,
} from '../utils/toolLabels'
import { useStickToBottom } from '../hooks/useStickToBottom'

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  partialThinking?: string
  streaming: boolean
  loading: boolean
  planMode?: boolean
  activeTools?: ActiveTool[]
  processSteps?: ProcessStep[]
  taskProgress?: TaskProgressInfo | null
  /** off | medium | full */
  toolProcessMode?: ToolProcessMode
  onEditUserMessage?: (msgId: string, content: string) => void
  onQuickPrompt?: (text: string) => void
  /** Regenerate from the user turn that produced this assistant message. */
  onRegenerate?: (assistantMsgId: string) => void
  /** Display name for the human (avatar + label). */
  userName?: string
}

/** Initials for avatar: "Alex" → A, "Mary Jane" → MJ */
function userInitials(name: string | undefined | null): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}

function firstName(name: string | undefined | null): string {
  const t = (name || '').trim()
  if (!t) return 'You'
  return t.split(/\s+/)[0] || 'You'
}

const STARTERS = [
  { label: 'What can you help with?', text: 'What can you help me with on this machine?' },
  { label: 'Explore this project', text: 'Scan the open project and summarize structure, stack, and what I should know.' },
  { label: 'Generate an image', text: 'Generate an image with ComfyUI: a cozy desk at night with soft neon lights.' },
  { label: 'Plan a task', text: 'Help me plan: ' },
]

const COLLAPSE_CHARS = 1400

function formatTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  } catch {
    return null
  }
}

function langFromClass(className?: string): string {
  if (!className) return 'code'
  const m = /language-([\w+-]+)/.exec(className)
  return m?.[1] || 'code'
}

function CodeBlock({
  className,
  children,
  isUser,
}: {
  className?: string
  children: ReactNode
  isUser?: boolean
}) {
  const [copied, setCopied] = useState(false)
  const lang = langFromClass(className)
  const text = String(children ?? '').replace(/\n$/, '')

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      /* */
    }
  }, [text])

  return (
    <div className="code-block" data-user={isUser ? '1' : undefined}>
      <div className="code-block-header">
        <span>{lang}</span>
        <IconBtn title={copied ? 'Copied' : 'Copy code'} onClick={() => void copy()} active={copied}>
          {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
        </IconBtn>
      </div>
      <pre>
        <code className={className}>{children}</code>
      </pre>
    </div>
  )
}

function ThinkingPanel({ text, openDefault = false }: { text: string; openDefault?: boolean }) {
  const [open, setOpen] = useState(openDefault)
  if (!text.trim()) return null
  return (
    <div
      className="mb-2 rounded-md overflow-hidden"
      style={{ border: '1px solid var(--border)', background: 'var(--bg-primary)' }}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-2 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide"
        style={{ color: 'var(--text-muted)' }}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>Thinking</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div
          className="px-2.5 pb-2 text-xs whitespace-pre-wrap max-h-48 overflow-y-auto"
          style={{ color: 'var(--text-secondary)', lineHeight: 1.45 }}
        >
          {text}
        </div>
      )}
    </div>
  )
}

function MessageBubble({
  msg,
  partial,
  partialThinking,
  onEditUserMessage,
  streaming,
  toolProcessMode = 'off',
  isStreamingPartial = false,
  hideAvatar = false,
  onOpenImage,
  onRegenerate,
  userName,
}: {
  msg: ChatMessage
  partial?: string
  partialThinking?: string
  onEditUserMessage?: (msgId: string, content: string) => void
  streaming?: boolean
  toolProcessMode?: ToolProcessMode
  isStreamingPartial?: boolean
  hideAvatar?: boolean
  onOpenImage?: (src: string, alt?: string) => void
  onRegenerate?: (id: string) => void
  userName?: string
}) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const rawText = msg.content + (partial || '')
  const text = useMemo(
    () => (isUser || isSystem ? rawText : sanitizeAssistantText(rawText)),
    [rawText, isUser, isSystem],
  )
  const thinkingText = (msg.thinking || '') + (partialThinking || '')
  const showEdit =
    msg.role === 'user' && !msg.reverted && !!onEditUserMessage && !streaming
  const timeLabel = formatTime(msg.created_at)
  const long = text.length > COLLAPSE_CHARS
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const displayText =
    long && !expanded && !isStreamingPartial
      ? `${text.slice(0, COLLAPSE_CHARS).trimEnd()}…`
      : text

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

  const bubbleClass = isUser
    ? 'chat-bubble chat-bubble-user'
    : isSystem
      ? 'chat-bubble chat-bubble-system'
      : 'chat-bubble chat-bubble-assistant'

  const copyMsg = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      /* */
    }
  }, [text])

  const userLabel = firstName(userName)
  const userAv = userInitials(userName)

  const avatar = isUser || isSystem ? (
    <div
      className="flex-shrink-0 rounded-full flex items-center justify-center font-semibold"
      style={{
        width: 'var(--chat-avatar)',
        height: 'var(--chat-avatar)',
        fontSize: isUser && userAv.length > 1 ? '0.55rem' : '0.65rem',
        background: isUser ? 'var(--accent)' : 'var(--error)',
        color: '#fff',
        border: 'none',
        visibility: hideAvatar ? 'hidden' : 'visible',
      }}
      title={isUser ? userName || 'You' : 'System'}
      aria-hidden
    >
      {isUser ? userAv : '!'}
    </div>
  ) : (
    <div
      className="flex-shrink-0 rounded-full overflow-hidden flex items-center justify-center"
      style={{
        width: 'var(--chat-avatar)',
        height: 'var(--chat-avatar)',
        background: 'var(--bg-tertiary)',
        border: '1px solid var(--border)',
        visibility: hideAvatar ? 'hidden' : 'visible',
      }}
      aria-hidden
      title="Remedy"
    >
      <RemedyLogo size={14} />
    </div>
  )

  const histSteps =
    !isUser && !isSystem && !isStreamingPartial && toolProcessMode !== 'off'
      ? stepsFromMessageTools(msg.tool_calls || [], msg.tool_results || [])
      : []

  return (
    <div
      className={`group chat-row flex w-full px-3 flex-col ${
        isUser ? 'items-end' : isSystem ? 'items-center' : 'items-start'
      }`}
      style={{ paddingTop: 'var(--chat-pad-y)', paddingBottom: 'var(--chat-pad-y)' }}
    >
      {/* w-fit: cluster + bubble hug content; never stretch to the other side's width */}
      <div
        className={`chat-cluster relative flex items-end gap-1.5 ${
          isUser ? 'flex-row-reverse' : 'flex-row'
        }`}
      >
        {!isSystem && avatar}

        <div
          className={bubbleClass}
          style={{
            background: bubbleBg,
            color: bubbleFg,
            border: `1px solid ${bubbleBorder}`,
            borderRadius: 'var(--chat-bubble-radius)',
            padding: 'var(--chat-bubble-pad)',
            fontSize: 'var(--chat-font)',
          }}
        >
          {!isUser && !isSystem && (
            <div className="flex items-center gap-2 mb-0.5 w-fit max-w-full">
              <div
                className="text-[9px] font-semibold tracking-wide"
                style={{ color: 'var(--text-muted)' }}
              >
                Remedy
              </div>
              {timeLabel && (
                <div
                  className="text-[9px] opacity-0 group-hover:opacity-100 transition-opacity"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {timeLabel}
                </div>
              )}
            </div>
          )}

          {isUser && (
            <div className="flex items-center justify-end gap-1.5 mb-0.5 w-fit max-w-full ml-auto">
              <div
                className="text-[9px] font-semibold tracking-wide"
                style={{ color: 'inherit', opacity: 0.85 }}
              >
                {userLabel}
              </div>
              {timeLabel && (
                <div
                  className="text-[9px] opacity-0 group-hover:opacity-70 transition-opacity"
                  style={{ color: 'inherit' }}
                >
                  {timeLabel}
                </div>
              )}
            </div>
          )}

          {!isUser && !isSystem && (
            <ThinkingPanel
              text={thinkingText}
              openDefault={Boolean(isStreamingPartial && partialThinking)}
            />
          )}

          <div className="message-body chat-bubble-body">
            {displayText ? (
              <>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    pre({ children }) {
                      return <>{children}</>
                    },
                    img({ src, alt }) {
                      if (!src) return null
                      return (
                        <button
                          type="button"
                          className="block p-0 m-0 border-0 bg-transparent cursor-zoom-in w-full text-left"
                          onClick={() => onOpenImage?.(src, alt)}
                          title="Click to expand"
                        >
                          <img
                            src={src}
                            alt={alt || 'image'}
                            style={{
                              maxWidth: '100%',
                              borderRadius: 8,
                              marginTop: 8,
                              marginBottom: 8,
                              border: '1px solid var(--border)',
                            }}
                            loading="lazy"
                          />
                        </button>
                      )
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
                      return (
                        <CodeBlock className={className} isUser={isUser}>
                          {children}
                        </CodeBlock>
                      )
                    },
                  }}
                >
                  {displayText}
                </ReactMarkdown>
                {isStreamingPartial && <span className="stream-caret" aria-hidden />}
              </>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>
                {isStreamingPartial ? (
                  <>
                    {thinkingText ? 'Thinking' : 'Generating'}
                    <span className="stream-caret" aria-hidden />
                  </>
                ) : (
                  '(empty)'
                )}
              </span>
            )}
          </div>

          {long && !isStreamingPartial && (
            <div className="mt-1 flex" style={{ justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
              <IconBtn
                title={expanded ? 'Show less' : 'Show more'}
                onClick={() => setExpanded((e) => !e)}
              >
                {expanded ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
              </IconBtn>
            </div>
          )}

          {/* Icon actions: copy + edit only (no save) */}
          {!isSystem && !isStreamingPartial && text && (
            <div
              className="mt-1 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity"
              style={{ justifyContent: isUser ? 'flex-end' : 'flex-start' }}
            >
              <IconBtn
                title={copied ? 'Copied' : 'Copy'}
                onClick={() => void copyMsg()}
                active={copied}
              >
                {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
              </IconBtn>
              {showEdit && (
                <IconBtn
                  title="Edit"
                  onClick={() => onEditUserMessage?.(msg.id, msg.content ?? '')}
                >
                  <IconEdit size={13} />
                </IconBtn>
              )}
              {!isUser && onRegenerate && !streaming && (
                <IconBtn title="Regenerate" onClick={() => onRegenerate(msg.id)}>
                  <IconRefresh size={13} />
                </IconBtn>
              )}
            </div>
          )}
        </div>
      </div>
      {/* Process under answer — collapsed by default after turn */}
      {histSteps.length > 0 && (
        <div
          className="w-full mt-0.5"
          style={{
            maxWidth: toolProcessMode === 'full' ? '100%' : 'min(var(--chat-max-width), 100%)',
            paddingLeft: 'calc(var(--chat-avatar) + 0.35rem)',
          }}
        >
          <ProcessTrace mode={toolProcessMode} steps={histSteps} defaultCollapsed />
        </div>
      )}
    </div>
  )
}

export function MessageFeed({
  messages,
  partialText,
  partialThinking = '',
  streaming,
  loading,
  planMode,
  activeTools = [],
  processSteps = [],
  taskProgress = null,
  toolProcessMode = 'off',
  onEditUserMessage,
  onQuickPrompt,
  onRegenerate,
  userName,
}: MessageFeedProps) {
  const [lightbox, setLightbox] = useState<{ src: string; alt?: string } | null>(null)

  // Follow tokens, thinking, tools, process — unless user scrolls up.
  const processSig = processSteps
    .map(
      (s) =>
        `${s.id}:${s.status}:${(s.resultText || '').length}:${(s.argsText || '').length}`,
    )
    .join('|')
  const {
    setScroller,
    setContent,
    showJump,
    jumpLatest,
  } = useStickToBottom({
    followActive: streaming,
    alwaysOfferJump: messages.length > 2 || streaming,
    deps: [
      messages.length,
      partialText,
      partialThinking,
      streaming,
      activeTools.map((t) => `${t.name}:${t.status}`).join(','),
      processSig,
      taskProgress?.percent,
      taskProgress?.label,
      toolProcessMode,
    ],
  })

  const visible = useMemo(() => messages.filter((m) => !m.reverted), [messages])

  const feedItems = useMemo(() => {
    const items: Array<
      | { type: 'day'; key: string; label: string }
      | { type: 'msg'; msg: ChatMessage; hideAvatar: boolean; index: number }
    > = []
    let lastDay = ''
    let prevRole: string | null = null
    visible.forEach((msg, index) => {
      const dk = dayKey(msg.created_at)
      if (dk && dk !== lastDay) {
        lastDay = dk
        items.push({ type: 'day', key: dk, label: dayLabel(msg.created_at) })
        prevRole = null
      }
      const hideAvatar =
        msg.role !== 'system'
        && prevRole === msg.role
        && msg.role !== 'user'
      if (msg.role !== 'system') prevRole = msg.role
      items.push({ type: 'msg', msg, hideAvatar, index })
    })
    return items
  }, [visible])

  return (
    <div
      ref={setScroller}
      className="flex-1 overflow-y-auto message-feed py-2 min-h-0 relative"
    >
      <div ref={setContent} className="message-feed-content">
      {planMode && (
        <div
          className="mx-4 mt-2 mb-2 px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-2"
          style={{
            background: 'color-mix(in srgb, var(--accent) 12%, var(--bg-tertiary))',
            border: '1px solid var(--accent)',
            color: 'var(--accent)',
          }}
        >
          <span>{'\u{1F9E0}'}</span>
          Plan mode active — describing approach; no tools will be executed
        </div>
      )}

      {loading && visible.length === 0 && (
        <div className="px-4 py-8 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading messages...
        </div>
      )}

      {feedItems.map((item) => {
        if (item.type === 'day') {
          return (
            <div
              key={`day-${item.key}`}
              className="flex items-center gap-3 px-6 py-2"
            >
              <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
              <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                {item.label}
              </span>
              <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
            </div>
          )
        }
        const { msg, hideAvatar } = item
        return (
          <Fragment key={msg.id}>
            <MessageBubble
              msg={msg}
              onEditUserMessage={onEditUserMessage}
              streaming={streaming}
              toolProcessMode={toolProcessMode}
              hideAvatar={hideAvatar}
              onOpenImage={(src, alt) => setLightbox({ src, alt })}
              onRegenerate={onRegenerate}
              userName={userName}
            />
          </Fragment>
        )
      })}

      {streaming && (
        <div className="px-3">
          <TaskProgress
            streaming={streaming}
            activeTools={activeTools}
            progress={taskProgress}
            showToolDetails={toolProcessMode !== 'off'}
          />
          {toolProcessMode !== 'off' && processSteps.length > 0 && (
            <ProcessTrace mode={toolProcessMode} steps={processSteps} live />
          )}
        </div>
      )}

      {streaming && (partialText || partialThinking || activeTools.length === 0) && (
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
          partialThinking={partialThinking}
          toolProcessMode="off"
          isStreamingPartial
          onOpenImage={(src, alt) => setLightbox({ src, alt })}
        />
      )}

      {!loading && visible.length === 0 && !streaming && (
        <div
          className="flex flex-col items-center justify-center min-h-[14rem] gap-3 px-6 text-center py-10"
          style={{ color: 'var(--text-muted)' }}
        >
          <RemedyLogo size={36} framed />
          <div className="text-lg font-medium" style={{ color: 'var(--text-primary)' }}>
            Ready when you are
          </div>
          <div className="text-xs max-w-sm leading-relaxed">
            Ask anything, plan, research, or open a project to build.{' '}
            <code style={{ color: 'var(--accent)' }}>/help</code> lists commands and shortcuts.
          </div>
          {onQuickPrompt && (
            <div className="flex flex-wrap justify-center gap-2 mt-1 max-w-lg">
              {STARTERS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  className="starter-chip"
                  onClick={() => onQuickPrompt(s.text)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="text-[0.7rem] max-w-sm leading-relaxed mt-1" style={{ color: 'var(--text-muted)' }}>
            <code style={{ color: 'var(--accent)' }}>Enter</code> send ·{' '}
            <code style={{ color: 'var(--accent)' }}>Shift+Enter</code> new line ·{' '}
            <code style={{ color: 'var(--accent)' }}>@</code> files ·{' '}
            <code style={{ color: 'var(--accent)' }}>/</code> commands
          </div>
        </div>
      )}

      <div aria-hidden className="h-px w-full" />
      </div>

      {showJump && (
        <button
          type="button"
          className="scroll-latest-fab"
          onClick={jumpLatest}
          title="Jump to latest and resume auto-scroll"
          aria-label="Jump to latest"
        >
          ↓
        </button>
      )}

      <ImageLightbox
        src={lightbox?.src ?? null}
        alt={lightbox?.alt}
        onClose={() => setLightbox(null)}
      />
    </div>
  )
}
