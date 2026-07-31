import {
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
  memo,
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
import { ChatImage } from './ChatImage'
import { linkifyBareImagePaths } from '../utils/linkifyImages'
import { chatMarkdownUrlTransform } from '../utils/chatMarkdownUrl'
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
  isFullProcessMode,
  processDefaultCollapsed,
  showsProcessTrace,
  stepsFromMessageTools,
  type ProcessStep,
  type ToolProcessMode,
} from '../utils/toolLabels'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { DiffCode } from './DiffCode'

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  partialThinking?: string
  streaming: boolean
  loading: boolean
  /** Shown when history fetch fails (session list still works). */
  loadError?: string | null
  planMode?: boolean
  activeTools?: ActiveTool[]
  processSteps?: ProcessStep[]
  taskProgress?: TaskProgressInfo | null
  /** off | medium | full — never hides the chat answer */
  toolProcessMode?: ToolProcessMode
  onEditUserMessage?: (msgId: string, content: string) => void
  onQuickPrompt?: (text: string) => void
  /** Regenerate from the user turn that produced this assistant message. */
  onRegenerate?: (assistantMsgId: string) => void
  /** Display name for the human (avatar + label). */
  userName?: string
  /** Partner display name for assistant avatar initials (default Remedy). */
  partnerName?: string
  /**
   * Attach a marked-up (or plain) image from the viewer to the next user prompt.
   * Parent should route this into the composer attachment rail.
   */
  onAttachMarkup?: (file: File) => void | Promise<void>
  /** Older history available beyond the newest window. */
  hasOlder?: boolean
  loadingOlder?: boolean
  onLoadOlder?: () => void
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
  {
    label: 'What can you help with?',
    text: 'What can you help me with on this machine?',
  },
  {
    label: 'Explore this project',
    text: 'Scan the open project and summarize structure, stack, and what I should know first.',
  },
  {
    label: 'Fix something',
    text: 'Help me fix a bug: ',
  },
  {
    label: 'Plan a task',
    text: 'Help me plan: ',
  },
]

/** Do not collapse answers — user wants full provider text visible. */
const COLLAPSE_CHARS = Number.POSITIVE_INFINITY

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
        <span>{lang === 'diff' || lang === 'patch' ? `${lang} · changes` : lang}</span>
        <IconBtn title={copied ? 'Copied' : 'Copy code'} onClick={() => void copy()} active={copied}>
          {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
        </IconBtn>
      </div>
      <DiffCode text={text} className={className} />
    </div>
  )
}

function ThinkingPanel({
  text,
  openDefault = false,
}: {
  text: string
  openDefault?: boolean
}) {
  const [open, setOpen] = useState(openDefault)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  // Keep expanded while streaming or when Full mode opens by default.
  useEffect(() => {
    if (openDefault) setOpen(true)
  }, [openDefault])
  // Stick to bottom of thinking while it grows (unless user scrolled up).
  // Never force-scroll when openDefault alone — that trapped the panel during streams.
  useEffect(() => {
    const el = bodyRef.current
    if (!el || !open) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) {
      el.scrollTop = el.scrollHeight
    }
  }, [text, open])
  if (!text.trim()) return null
  // Flat strip with left accent — same look at every process mode.
  return (
    <div
      className={`thinking-panel mb-2 w-full min-w-0 ${open ? 'thinking-panel-open' : ''}`}
    >
      <button
        type="button"
        className="thinking-panel-toggle w-full flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>
          Thinking
          {text.length > 80 ? ` · ${text.length.toLocaleString()} chars` : ''}
        </span>
        <span aria-hidden className="thinking-chevron">
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div ref={bodyRef} className="thinking-panel-body">
          {text}
        </div>
      )}
    </div>
  )
}

const MessageBubble = memo(function MessageBubble({
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
  partnerName,
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
  partnerName?: string
}) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const rawText = msg.content + (partial || '')
  const text = useMemo(
    () => (isUser || isSystem ? rawText : sanitizeAssistantText(rawText)),
    [rawText, isUser, isSystem],
  )
  const thinkingText = (msg.thinking || '') + (partialThinking || '')
  const openThinkingByDefault = isFullProcessMode(toolProcessMode)
  const showEdit =
    msg.role === 'user' && !msg.reverted && !!onEditUserMessage && !streaming
  const timeLabel = formatTime(msg.created_at)
  const long = text.length > COLLAPSE_CHARS
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  // Answer text is never truncated by process mode (COLLAPSE_CHARS is infinite).
  const rawDisplay =
    long && !expanded && !isStreamingPartial
      ? `${text.slice(0, COLLAPSE_CHARS).trimEnd()}…`
      : text
  // Models often paste bare Windows paths — promote to markdown images for ChatImage.
  const displayText = linkifyBareImagePaths(rawDisplay)

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
  const partnerLabel = firstName(partnerName || 'Remedy')
  const partnerAv = userInitials(partnerName || 'Remedy')

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
      className="flex-shrink-0 rounded-full flex items-center justify-center font-semibold"
      style={{
        width: 'var(--chat-avatar)',
        height: 'var(--chat-avatar)',
        fontSize: partnerAv.length > 1 ? '0.55rem' : '0.65rem',
        // Match user-side avatar style; initials from partner display name
        background: 'color-mix(in srgb, var(--accent) 55%, var(--bg-tertiary))',
        color: '#fff',
        border: '1px solid var(--border)',
        visibility: hideAvatar ? 'hidden' : 'visible',
      }}
      aria-hidden
      title={partnerName || 'Remedy'}
    >
      {partnerAv}
    </div>
  )

  const histSteps =
    !isUser && !isSystem && !isStreamingPartial && showsProcessTrace(toolProcessMode)
      ? stepsFromMessageTools(msg.tool_calls || [], msg.tool_results || [])
      : []

  return (
    <div
      className={`group chat-row flex w-full px-3 flex-col ${
        isUser ? 'items-end' : isSystem ? 'items-center' : 'items-start'
      }`}
      style={{ paddingTop: 'var(--chat-pad-y)', paddingBottom: 'var(--chat-pad-y)' }}
    >
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
            <div className="chat-meta flex items-center gap-2 mb-1 w-fit max-w-full">
              <div className="chat-meta-label">{partnerLabel}</div>
              {timeLabel && <div className="chat-meta-time">{timeLabel}</div>}
            </div>
          )}

          {isUser && (
            <div className="chat-meta chat-meta-user flex items-center justify-end gap-1.5 mb-1 w-fit max-w-full ml-auto">
              <div className="chat-meta-label" style={{ color: 'inherit', opacity: 0.9 }}>
                {userLabel}
              </div>
              {timeLabel && (
                <div className="chat-meta-time" style={{ color: 'inherit', opacity: 0.75 }}>
                  {timeLabel}
                </div>
              )}
            </div>
          )}

          {!isUser && !isSystem && (
            <ThinkingPanel
              text={thinkingText}
              openDefault={
                Boolean(isStreamingPartial && partialThinking) || openThinkingByDefault
              }
            />
          )}

          <div className="message-body chat-bubble-body">
            {displayText ? (
              <>
                {/* Plain text while streaming — markdown only after finalize (snappier). */}
                {isStreamingPartial && !isUser && !isSystem ? (
                  <div className="stream-plain whitespace-pre-wrap break-words">
                    {displayText}
                    <span className="stream-caret" aria-hidden>
                      ▍
                    </span>
                  </div>
                ) : (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    // Default strip data:/Windows paths → blank images in chat.
                    urlTransform={chatMarkdownUrlTransform}
                    components={{
                      pre({ children }) {
                        return <>{children}</>
                      },
                      a({ href, children }) {
                        const h = (href || '').trim()
                        // Only allow safe navigable schemes in chat markdown.
                        if (!h || !/^(https?:|mailto:)/i.test(h)) {
                          return <span>{children}</span>
                        }
                        if (/^mailto:/i.test(h)) {
                          return (
                            <a href={h} rel="noopener noreferrer">
                              {children}
                            </a>
                          )
                        }
                        // Double-click → in-rail Browser. Ctrl/Cmd+click → system browser.
                        return (
                          <a
                            href={h}
                            className="chat-rail-link"
                            title="Double-click: open in Browser rail · Ctrl+click: system browser"
                            onClick={(e) => {
                              // Prevent accidental leave of the app on single click
                              if (!e.ctrlKey && !e.metaKey) {
                                e.preventDefault()
                              }
                            }}
                            onDoubleClick={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              try {
                                window.dispatchEvent(
                                  new CustomEvent('remedy:computer-ui', {
                                    detail: { openBrowser: true },
                                  }),
                                )
                                window.dispatchEvent(
                                  new CustomEvent('remedy:browser-set-url', {
                                    detail: { url: h, navigate: true },
                                  }),
                                )
                              } catch {
                                window.open(h, '_blank', 'noopener,noreferrer')
                              }
                            }}
                          >
                            {children}
                          </a>
                        )
                      },
                      img({ src, alt }) {
                        if (!src) {
                          return (
                            <span
                              className="chat-img-error text-xs block my-1 px-2 py-1 rounded"
                              style={{
                                color: 'var(--warning)',
                                background: 'var(--bg-tertiary)',
                                border: '1px solid var(--border)',
                              }}
                            >
                              Image unavailable{alt ? `: ${alt}` : ''}
                            </span>
                          )
                        }
                        // Stable key: remounting re-fetches media and flickers.
                        // data: URIs are huge — key on length+prefix only.
                        const imgKey =
                          src.length > 96
                            ? `img-${alt || 'x'}-${src.length}-${src.slice(0, 48)}`
                            : `img-${src}`
                        return (
                          <ChatImage
                            key={imgKey}
                            src={src}
                            alt={alt}
                            onOpen={(url, a) => onOpenImage?.(url, a)}
                          />
                        )
                      },
                      code({ children, className }) {
                        const inline = !className
                        if (inline) {
                          return (
                            <code className={isUser ? 'chat-inline-code user' : 'chat-inline-code'}>
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
                )}
              </>
            ) : (
              <span className="chat-empty-placeholder">
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

          {!isSystem && !isStreamingPartial && text && (
            <div
              className="chat-actions mt-1.5 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity"
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
      {/* Process under answer — same chrome at every Min/Med/Full depth */}
      {histSteps.length > 0 && showsProcessTrace(toolProcessMode) && (
        <div
          className="process-under-answer w-full mt-1"
          style={{
            maxWidth: 'min(var(--chat-max-width), 100%)',
            paddingLeft: 'calc(var(--chat-avatar) + 0.35rem)',
          }}
        >
          <ProcessTrace
            mode={toolProcessMode}
            steps={histSteps}
            defaultCollapsed={processDefaultCollapsed(toolProcessMode)}
          />
        </div>
      )}
    </div>
  )
})

export function MessageFeed({
  messages,
  partialText,
  partialThinking = '',
  streaming,
  loading,
  loadError = null,
  planMode,
  activeTools = [],
  processSteps = [],
  taskProgress = null,
  toolProcessMode = 'off',
  onEditUserMessage,
  onQuickPrompt,
  onRegenerate,
  userName,
  partnerName = 'Remedy',
  onAttachMarkup,
  hasOlder = false,
  loadingOlder = false,
  onLoadOlder,
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

  // Window long chats: only mount recent messages (virtualization lite) unless expanded.
  const WINDOW = 80
  const [showAll, setShowAll] = useState(false)
  const windowed = useMemo(() => {
    if (showAll || visible.length <= WINDOW) return visible
    return visible.slice(-WINDOW)
  }, [visible, showAll])
  const hiddenCount = Math.max(0, visible.length - windowed.length)

  const feedItems = useMemo(() => {
    const items: Array<
      | { type: 'day'; key: string; label: string }
      | { type: 'msg'; msg: ChatMessage; hideAvatar: boolean; index: number }
    > = []
    let lastDay = ''
    let prevRole: string | null = null
    windowed.forEach((msg, index) => {
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
  }, [windowed])

  return (
    <div
      ref={setScroller}
      className="message-feed py-2 relative w-full"
      style={{
        position: 'absolute',
        inset: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
      }}
    >
      <div ref={setContent} className="message-feed-content min-h-full flex flex-col">
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
          Plan mode active — explore and save a structured plan; shell/file tools blocked until Build (Ctrl+B)
        </div>
      )}

      {loading && visible.length === 0 && (
        <div className="px-4 py-8 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading messages...
        </div>
      )}

      {loadError && !loading && (
        <div
          className="mx-4 mt-3 mb-2 px-3 py-2 rounded-md text-xs"
          style={{
            background: 'color-mix(in srgb, var(--error) 12%, var(--bg-tertiary))',
            border: '1px solid var(--error)',
            color: 'var(--error)',
          }}
          role="alert"
        >
          Could not load chat history: {loadError}
        </div>
      )}

      {hasOlder && !loading && (
        <div className="flex justify-center px-4 py-2">
          <button
            type="button"
            disabled={loadingOlder}
            onClick={() => onLoadOlder?.()}
            className="text-xs px-3 py-1.5 rounded-full border"
            style={{
              color: 'var(--text-muted)',
              borderColor: 'var(--border)',
              background: 'var(--bg-tertiary)',
              opacity: loadingOlder ? 0.6 : 1,
              cursor: loadingOlder ? 'wait' : 'pointer',
            }}
          >
            {loadingOlder ? 'Loading earlier…' : 'Load earlier messages'}
          </button>
        </div>
      )}

      {hiddenCount > 0 && (
        <div className="px-4 py-2 text-center">
          <button
            type="button"
            className="text-xs px-3 py-1 rounded"
            style={{
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              background: 'var(--bg-tertiary)',
            }}
            onClick={() => setShowAll(true)}
          >
            Show {hiddenCount} earlier messages
          </button>
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
              partnerName={partnerName}
            />
          </Fragment>
        )
      })}

      {streaming && (
        <div
          className="px-3 overflow-y-auto"
          style={{
            // Room for Process depth without eating the whole feed
            maxHeight:
              toolProcessMode === 'full'
                ? 'min(42vh, 26rem)'
                : toolProcessMode === 'medium'
                  ? 'min(34vh, 20rem)'
                  : 'min(26vh, 14rem)',
          }}
        >
          {/*
            Progress bar always. Tool chips only if ProcessTrace is absent —
            otherwise every mode double-lists the same steps.
          */}
          <TaskProgress
            streaming={streaming}
            activeTools={activeTools}
            progress={taskProgress}
            showToolDetails={processSteps.length === 0}
          />
          {processSteps.length > 0 && (
            <ProcessTrace mode={toolProcessMode} steps={processSteps} live />
          )}
        </div>
      )}

      {/* Live thinking + answer always docked at the visual bottom of the feed. */}
      {streaming && (
        <div
          className="sticky bottom-0 z-20 pt-1 pb-2"
          style={{
            background:
              'linear-gradient(180deg, transparent 0%, var(--bg-primary) 18%, var(--bg-primary) 100%)',
          }}
        >
          <div
            className="mx-2 rounded-xl border"
            style={{
              borderColor: 'var(--border)',
              background: 'var(--bg-secondary)',
              boxShadow: '0 -6px 24px color-mix(in srgb, var(--bg-primary) 70%, transparent)',
            }}
          >
            <div
              className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide flex items-center gap-1.5"
              style={{ color: 'var(--accent)', borderBottom: '1px solid var(--border)' }}
            >
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{
                  background: 'var(--accent)',
                  animation: 'pulse 1.2s ease infinite',
                }}
              />
              Live · thinking & answer
            </div>
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
              toolProcessMode={toolProcessMode}
              isStreamingPartial
              onOpenImage={(src, alt) => setLightbox({ src, alt })}
              partnerName={partnerName}
            />
          </div>
        </div>
      )}

      {!loading && visible.length === 0 && !streaming && (
        <div
          className="flex flex-col items-center justify-center gap-3 px-6 text-center"
          style={{
            color: 'var(--text-muted)',
            minHeight: 'min(100%, 22rem)',
            flex: 1,
            paddingTop: '3rem',
            paddingBottom: '3rem',
          }}
        >
          {/* Monogram only — no rounded plate; ~20% larger than prior 77px. */}
          <RemedyLogo size={92} variant="auto" title="Remedy" />
          <div
            className="text-lg font-semibold tracking-tight"
            style={{ color: 'var(--text-primary)' }}
          >
            {userName?.trim()
              ? `Ready when you are, ${firstName(userName)}`
              : 'Your partner is ready'}
          </div>
          <div className="text-xs max-w-sm leading-relaxed">
            Ask anything, plan, research, or open a project to build.{' '}
            <code style={{ color: 'var(--accent)' }}>/help</code> lists commands ·{' '}
            <code style={{ color: 'var(--accent)' }}>F1</code> opens the Help wiki.
          </div>
          {onQuickPrompt && (
            <div
              className="flex flex-wrap justify-center gap-2 mt-1 max-w-lg"
              role="group"
              aria-label="Starter prompts"
            >
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
          <div
            className="text-[0.7rem] max-w-sm leading-relaxed mt-1"
            style={{ color: 'var(--text-muted)' }}
          >
            <code style={{ color: 'var(--accent)' }}>Enter</code> send ·{' '}
            <code style={{ color: 'var(--accent)' }}>Shift+Enter</code> new line ·{' '}
            <code style={{ color: 'var(--accent)' }}>@</code> files ·{' '}
            <code style={{ color: 'var(--accent)' }}>/</code> commands ·{' '}
            <code style={{ color: 'var(--accent)' }}>Shift+Tab</code> Plan/Build
          </div>
          <div
            className="text-[0.7rem] max-w-sm leading-relaxed italic"
            style={{ color: 'var(--text-muted)', opacity: 0.9 }}
          >
            My name is Ahmi — I hope you enjoy my Remedy.
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
        onAttachMarkup={onAttachMarkup}
      />
    </div>
  )
}
