import {
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
  useContext,
  createContext,
  memo,
  type ReactNode,
  Fragment,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'
import { sanitizeAssistantText } from '../utils/sanitizeChat'
import { dayKey, dayLabel } from '../utils/relativeTime'
import { BuildTodos, type BuildTodo } from './BuildTodos'
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
  /** Live build checklist (todo_write) — crossed off as Remedy completes items. */
  buildTodos?: BuildTodo[]
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
  /** Active project path for empty-state context. */
  projectPath?: string | null
  /** Focused session — close the image viewer on switch so a revoked/stale src cannot linger. */
  sessionId?: string | null
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
    label: 'Review recent changes',
    text: 'Review recent git changes and flag risks or follow-ups.',
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

type OpenImageFn = (src: string, alt?: string) => void

const ChatMdCtx = createContext<{
  onOpenImage?: OpenImageFn
  isUser: boolean
}>({ isUser: false })

function MarkdownImg({ src, alt }: { src?: string; alt?: string }) {
  const { onOpenImage } = useContext(ChatMdCtx)
  if (!src) {
    return (
      <span
        className="chat-img-chip inline-flex items-center gap-1.5 my-1 px-2.5 py-1 rounded-lg text-xs"
        style={{
          color: 'var(--text-secondary)',
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
        }}
      >
        <span aria-hidden>📎</span>
        {alt || 'attachment'}
        <span className="opacity-60">· not previewed</span>
      </span>
    )
  }
  return <ChatImage src={src} alt={alt} onOpen={onOpenImage} />
}

function MarkdownLink({ href, children }: { href?: string; children?: ReactNode }) {
  const h = (href || '').trim()
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
  return (
    <a
      href={h}
      className="chat-rail-link"
      title="Double-click: open in Browser rail · Ctrl+click: system browser"
      onClick={(e) => {
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
}

function MarkdownCode({
  children,
  className,
}: {
  children?: ReactNode
  className?: string
}) {
  const { isUser } = useContext(ChatMdCtx)
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
}

/** Stable identities — a new `components` map remounts every <img> on each stream token. */
const CHAT_MD_COMPONENTS = {
  pre({ children }: { children?: ReactNode }) {
    return <>{children}</>
  },
  a: MarkdownLink,
  img: MarkdownImg,
  code: MarkdownCode,
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
  const mdCtx = useMemo(
    () => ({ onOpenImage, isUser }),
    [onOpenImage, isUser],
  )

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
      className={`group chat-row flex w-full flex-col ${
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

          <div
            className={`message-body chat-bubble-body${
              isStreamingPartial && !isUser && !isSystem ? ' stream-md' : ''
            }`}
          >
            {displayText ? (
              <>
                {/* Live markdown while streaming for smoother final morph. */}
                <ChatMdCtx.Provider value={mdCtx}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  urlTransform={chatMarkdownUrlTransform}
                  components={CHAT_MD_COMPONENTS}
                >
                  {displayText}
                </ReactMarkdown>
                </ChatMdCtx.Provider>
                {isStreamingPartial && !isUser && !isSystem ? (
                  <span className="stream-caret" aria-hidden>
                    ▍
                  </span>
                ) : null}
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
              className="chat-actions mt-1.5 flex items-center transition-opacity"
              style={{
                justifyContent: isUser ? 'flex-end' : 'flex-start',
                marginLeft: isUser ? 'auto' : undefined,
              }}
            >
              <IconBtn
                title={copied ? 'Copied' : 'Copy message'}
                onClick={() => void copyMsg()}
                active={copied}
              >
                {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
              </IconBtn>
              {showEdit && (
                <IconBtn
                  title="Edit & resend"
                  onClick={() => onEditUserMessage?.(msg.id, msg.content ?? '')}
                >
                  <IconEdit size={13} />
                </IconBtn>
              )}
              {!isUser && onRegenerate && !streaming && (
                <IconBtn title="Regenerate reply" onClick={() => onRegenerate(msg.id)}>
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
  buildTodos = [],
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
  projectPath = null,
  sessionId = null,
}: MessageFeedProps) {
  const [lightbox, setLightbox] = useState<{ src: string; alt?: string } | null>(null)
  const openLightbox = useCallback((src: string, alt?: string) => {
    setLightbox({ src, alt })
  }, [])
  const closeLightbox = useCallback(() => setLightbox(null), [])

  useEffect(() => {
    setLightbox(null)
  }, [sessionId])

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
          className="mt-2 mb-2 px-3 py-2 rounded-xl text-xs font-medium flex items-center gap-2"
          style={{
            background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-secondary))',
            border: '1px solid color-mix(in srgb, var(--accent) 40%, var(--border))',
            color: 'var(--accent)',
          }}
        >
          <span aria-hidden>◇</span>
          <span>
            <strong>Plan mode</strong>
            {' — '}explore and save a structured plan; shell/file tools unlock in Build
            {' '}
            <kbd style={{ opacity: 0.85 }}>Ctrl+B</kbd>
          </span>
        </div>
      )}

      {loading && visible.length === 0 && (
        <div className="chat-skeleton" aria-busy="true" aria-label="Loading conversation">
          <div className="chat-skeleton-row is-user" />
          <div className="chat-skeleton-row is-assistant" />
          <div className="chat-skeleton-row is-assistant" style={{ width: '48%' }} />
          <div className="chat-skeleton-row is-user" style={{ width: '40%' }} />
          <div className="chat-skeleton-row is-assistant" style={{ width: '65%' }} />
        </div>
      )}

      {loadError && !loading && (
        <div
          className="mt-3 mb-2 px-3 py-2 rounded-xl text-xs"
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
        <div className="flex justify-center py-2">
          <button
            type="button"
            disabled={loadingOlder}
            onClick={() => onLoadOlder?.()}
            className="chat-load-pill"
          >
            {loadingOlder ? 'Loading earlier…' : 'Load earlier messages'}
          </button>
        </div>
      )}

      {hiddenCount > 0 && (
        <div className="flex justify-center py-2">
          <button
            type="button"
            className="chat-load-pill"
            onClick={() => setShowAll(true)}
          >
            Show {hiddenCount} earlier messages
          </button>
        </div>
      )}

      {feedItems.map((item) => {
        if (item.type === 'day') {
          return (
            <div key={`day-${item.key}`} className="chat-day-divider">
              <span className="chat-day-label">{item.label}</span>
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
              onOpenImage={openLightbox}
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
          <BuildTodos items={buildTodos} live />
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
      {!streaming && buildTodos.length > 0 && (
        <BuildTodos items={buildTodos} />
      )}

      {streaming && (
        <div className="live-stream-dock">
          <div className="live-stream-card">
            <div className="live-stream-header">
              <span className="live-stream-dot" aria-hidden />
              <span>Live</span>
              <span style={{ color: 'var(--text-muted)', fontWeight: 500, letterSpacing: 0 }}>
                · thinking & answer
              </span>
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
              onOpenImage={openLightbox}
              partnerName={partnerName}
            />
          </div>
        </div>
      )}

      {!loading && visible.length === 0 && !streaming && (
        <div className="chat-empty-hero">
          <RemedyLogo size={88} variant="auto" title="Remedy" />
          <div className="chat-empty-title">
            {userName?.trim()
              ? `Ready when you are, ${firstName(userName)}`
              : 'Your partner is ready'}
          </div>
          <div className="chat-empty-sub">
            {projectPath ? (
              <>
                Project folder attached — ask to explore, review, or implement.
                <br />
                <code className="text-[0.7em] break-all">{projectPath}</code>
              </>
            ) : (
              <>
                Ask anything, plan, research, or open a project to build.{' '}
                <code>/help</code> lists commands · <code>F1</code> opens the Help wiki.
              </>
            )}
          </div>
          {onQuickPrompt && (
            <div
              className="flex flex-wrap justify-center gap-2 mt-2 max-w-lg"
              role="group"
              aria-label="Starter prompts"
            >
              {STARTERS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  className="starter-chip"
                  title={s.text}
                  onClick={() => onQuickPrompt(s.text)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="chat-empty-sub mt-0.5" style={{ maxWidth: '26rem', opacity: 0.85 }}>
            <code>Enter</code> send · <code>Shift+Enter</code> new line ·{' '}
            <code>@</code> files · <code>/</code> commands · <code>Shift+Tab</code> Plan/Build
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
        onClose={closeLightbox}
        onAttachMarkup={onAttachMarkup}
      />
    </div>
  )
}
