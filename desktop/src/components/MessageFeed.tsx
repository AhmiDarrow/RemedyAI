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
  type PointerEvent as ReactPointerEvent,
  Fragment,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../types'
import { sanitizeAssistantText } from '../utils/sanitizeChat'
import { dayKey, dayLabel } from '../utils/relativeTime'
import { intlLocale } from '../i18n/locale'
import { BuildTodos, todosHaveOpen, type BuildTodo } from './BuildTodos'
import type { TaskProgressInfo } from './TaskProgress'
import { ImageLightbox } from './ImageLightbox'
import { ChatImage } from './ChatImage'
import { linkifyBareImagePaths } from '../utils/linkifyImages'
import { chatMarkdownUrlTransform, remoteImageHost } from '../utils/chatMarkdownUrl'
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
import { LiveTurnView } from './LiveTurnView'
import { WorkStrip } from './WorkStrip'
import { ReactionPicker, ReactionPills } from './Reactions'
import { FeedScrollContext } from './feedScroll'
import { assistantBeats, splitLiveText, splitSavedReply } from '../utils/liveSegments'
import { turnIdForMessages } from '../utils/workTurns'
import {
  showsProcessTrace,
  stepsFromMessageTools,
  type ProcessStep,
  type ToolProcessMode,
} from '../utils/toolLabels'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { feedHiddenCount, feedNextChunk, feedWindowStart } from '../utils/feedWindow'
import { DiffCode } from './DiffCode'
import { useI18n } from '../i18n'

export type ActiveTool = { name: string; status: 'running' | 'done' | 'error' }

export interface MessageFeedProps {
  messages: ChatMessage[]
  partialText: string
  /** Offsets where tool calls split interim narration from the live reply. */
  textBreaks?: number[]
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
  /** Studio split: thinking/tools/BUILD render in the actions pane, not here. */
  hideWork?: boolean
  /** Click a Remedy bubble to scroll the actions pane to that turn's work. */
  onSelectTurn?: (turnId: string) => void
  onEditUserMessage?: (msgId: string, content: string) => void
  onQuickPrompt?: (text: string) => void
  /** Empty-state starter chips — Studio set by default; Grove passes its own. */
  starters?: { label: string; text: string }[]
  /** Empty-state subtitle override (surface-specific tone). */
  emptySub?: ReactNode
  /** Empty-state keyboard-hint row override. */
  emptyHints?: ReactNode
  /** Regenerate from the user turn that produced this assistant message. */
  onRegenerate?: (assistantMsgId: string) => void
  /** Owner reacts to one of Remedy's messages ('' clears). */
  onReact?: (messageId: string, emoji: string) => void
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
  /**
   * Bumps on every composer submit (Enter, send, steer, queue). Forces the
   * feed to the floor even when the user was reading history — Jump to latest
   * stays for scroll-up *during* a stream with no new prompt.
   */
  /** Bumped by the send flow when a prompt actually goes out — re-pins the feed. */
  stickNonce: number
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

/** Do not collapse answers — user wants full provider text visible. */
const COLLAPSE_CHARS = Number.POSITIVE_INFINITY

function formatTime(iso: string | null | undefined, locale?: string): string | null {
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleTimeString(locale ? [locale] : [], { hour: 'numeric', minute: '2-digit' })
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
  const { onOpenImage, isUser } = useContext(ChatMdCtx)
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
  if (!isUser && remoteImageHost(src)) {
    return <RemoteImageGate src={src} alt={alt} onOpen={onOpenImage} />
  }
  return <ChatImage src={src} alt={alt} onOpen={onOpenImage} />
}

/**
 * A remote image in a reply loads only when the owner clicks. Painting it
 * automatically would let a prompt-injected reply send data to any server
 * through the image URL.
 */
function RemoteImageGate({ src, alt, onOpen }: { src: string; alt?: string; onOpen?: OpenImageFn }) {
  const [load, setLoad] = useState(false)
  if (load) return <ChatImage src={src} alt={alt} onOpen={onOpen} />
  const host = remoteImageHost(src) || 'the web'
  return (
    <button
      type="button"
      data-testid="remote-image-gate"
      className="chat-img-chip inline-flex items-center gap-1.5 my-1 px-2.5 py-1 rounded-lg text-xs"
      title={`Load image from ${host}`}
      onClick={() => setLoad(true)}
      style={{
        color: 'var(--text-secondary)',
        background: 'var(--bg-tertiary)',
        border: '1px solid var(--border)',
        cursor: 'pointer',
      }}
    >
      <span aria-hidden>🖼</span>
      {alt || 'image'}
      <span className="opacity-60">· from {host} · click to load</span>
    </button>
  )
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
  sessionId = null,
  textBreaks = [],
  onReact,
  hideWork = false,
  turnId,
  onSelectTurn,
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
  sessionId?: string | null
  /** Live turn offsets; mid-turn speech becomes its own bubble at each break. */
  textBreaks?: number[]
  /** The owner reacts to Remedy's messages (one per message; same again removes). */
  onReact?: (messageId: string, emoji: string) => void
  hideWork?: boolean
  turnId?: string | null
  onSelectTurn?: (turnId: string) => void
}) {
  const { resolved } = useI18n()
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const isAssistant = !isUser && !isSystem
  // User-facing prose stays in the bubble (including text written before a
  // tool call). Thinking and tool traces belong to the Working strip.
  const rawText = msg.content + (partial || '')
  const beats = useMemo(() => {
    if (!isAssistant) return []
    const segs = isStreamingPartial
      ? splitLiveText(rawText, textBreaks)
      : splitSavedReply(rawText, msg.tool_calls || [])
    return assistantBeats(segs).map((b) => ({
      ...b,
      text: linkifyBareImagePaths(sanitizeAssistantText(b.text)),
    }))
  }, [isAssistant, isStreamingPartial, rawText, textBreaks, msg.tool_calls])
  const text = useMemo(
    () => (isUser || isSystem ? rawText : beats.map((b) => b.text).join('\n\n') || sanitizeAssistantText(rawText)),
    [rawText, isUser, isSystem, beats],
  )
  const thinkingText = (msg.thinking || '') + (partialThinking || '')
  const showEdit =
    msg.role === 'user' && !msg.reverted && !!onEditUserMessage && !streaming
  const timeLabel = formatTime(msg.created_at, intlLocale(resolved))
  const lastBeatText = beats.length ? beats[beats.length - 1]!.text : text
  const long = lastBeatText.length > COLLAPSE_CHARS
  const [expanded, setExpanded] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  // Touch: a long press on the bubble opens the reaction picker.
  const pressTimer = useRef<number | null>(null)
  const canReact = Boolean(onReact) && msg.role === 'assistant' && !isStreamingPartial
  const clearPress = () => {
    if (pressTimer.current != null) window.clearTimeout(pressTimer.current)
    pressTimer.current = null
  }
  const pressHandlers = canReact
    ? {
        onPointerDown: (e: ReactPointerEvent) => {
          if (e.pointerType !== 'touch') return
          clearPress()
          pressTimer.current = window.setTimeout(() => setPickerOpen(true), 450)
        },
        onPointerUp: clearPress,
        onPointerCancel: clearPress,
        onPointerMove: clearPress,
      }
    : {}
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
        color: isUser ? 'var(--accent-foreground)' : 'var(--error-foreground)',
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
        background: 'var(--bg-tertiary)',
        color: 'var(--text-primary)',
        border: '1px solid var(--border)',
        visibility: hideAvatar ? 'hidden' : 'visible',
      }}
      aria-hidden
      title={partnerName || 'Remedy'}
    >
      {partnerAv}
    </div>
  )

  const histSteps = useMemo(
    () =>
      isAssistant && !isStreamingPartial && showsProcessTrace(toolProcessMode)
        ? stepsFromMessageTools(msg.tool_calls || [], msg.tool_results || [], {
            requestId: msg.request_id,
          })
        : [],
    [isAssistant, isStreamingPartial, toolProcessMode, msg.tool_calls, msg.tool_results, msg.request_id],
  )
  const savedThinking = isAssistant && !isStreamingPartial ? (msg.thinking || '').trim() : ''
  const showStrip = histSteps.length > 0 || savedThinking.length > 0
  // A tools-only turn has no reply; the strip says everything.
  const placeholderOnly = /^\*\(Used tools — see process\.\)\*$/.test(text.trim())
  const showBubble = !(showStrip && (!text.trim() || placeholderOnly))

  const selectTurn = (e: ReactPointerEvent) => {
    if (!onSelectTurn || !turnId || isUser || isSystem) return
    if ((e.target as HTMLElement).closest('button, a, input, textarea')) return
    onSelectTurn(turnId)
  }

  return (
    <div
      className={`group chat-row flex w-full flex-col ${
        isUser ? 'items-end' : isSystem ? 'items-center' : 'items-start'
      }`}
      data-turn-id={turnId || undefined}
      onClick={onSelectTurn && turnId && isAssistant ? selectTurn : undefined}
      style={{ paddingTop: 'var(--chat-pad-y)', paddingBottom: 'var(--chat-pad-y)' }}
    >
      {showStrip && !hideWork && (
        <div className="work-strip-slot">
          <WorkStrip
            steps={histSteps}
            thinking={savedThinking}
            mode={toolProcessMode}
            defaultOpen={false}
            sessionId={sessionId}
          />
        </div>
      )}
      {showBubble && (
      <div
        className={`chat-cluster relative flex gap-1.5 ${
          isUser ? 'flex-row-reverse items-end' : 'flex-row items-start'
        }`}
      >
        {!isSystem && avatar}

        {isAssistant && beats.length > 0 ? (
        <div className="chat-beats" data-testid="assistant-beats">
          {beats.map((beat, i) => {
            const isLast = i === beats.length - 1
            const body = isLast && long && !expanded && !isStreamingPartial
              ? `${beat.text.slice(0, COLLAPSE_CHARS).trimEnd()}…`
              : beat.text
            return (
              <div
                key={`${msg.id}:beat:${i}`}
                className={`${bubbleClass} chat-beat is-${beat.kind}`}
                data-testid={beat.kind === 'final' ? 'assistant-beat-final' : 'assistant-beat'}
                {...(isLast ? pressHandlers : {})}
                style={{
                  background: bubbleBg,
                  color: bubbleFg,
                  border: `1px solid ${bubbleBorder}`,
                  borderRadius: 'var(--chat-bubble-radius)',
                  padding: 'var(--chat-bubble-pad)',
                  fontSize: 'var(--chat-font)',
                }}
              >
                {i === 0 && (
                  <div className="chat-meta flex items-center gap-2 mb-1 w-fit max-w-full">
                    <div className="chat-meta-label">{partnerLabel}</div>
                    {timeLabel && <div className="chat-meta-time">{timeLabel}</div>}
                    {beats.length === 1 && msg.model && (
                      <div className="chat-meta-time" title={`Answered by ${msg.model}`}>
                        {msg.model}
                      </div>
                    )}
                  </div>
                )}
                {beat.kind === 'final' && i > 0 && msg.model && (
                  <div className="chat-meta flex items-center gap-2 mb-1 w-fit max-w-full">
                    <div className="chat-meta-time" title={`Answered by ${msg.model}`}>
                      {msg.model}
                    </div>
                  </div>
                )}
                <div
                  className={`message-body chat-bubble-body${
                    isStreamingPartial && isLast ? ' stream-md' : ''
                  }`}
                >
                  {body ? (
                    <>
                      <ChatMdCtx.Provider value={mdCtx}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        urlTransform={chatMarkdownUrlTransform}
                        components={CHAT_MD_COMPONENTS}
                      >
                        {body}
                      </ReactMarkdown>
                      </ChatMdCtx.Provider>
                      {isStreamingPartial && isLast ? (
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
                {isLast && long && !isStreamingPartial && (
                  <div className="mt-1 flex" style={{ justifyContent: 'flex-start' }}>
                    <IconBtn
                      title={expanded ? 'Show less' : 'Show more'}
                      onClick={() => setExpanded((e) => !e)}
                    >
                      {expanded ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
                    </IconBtn>
                  </div>
                )}
                {isLast && !isStreamingPartial && text && (
                  <div
                    className="chat-actions mt-1.5 flex items-center transition-opacity"
                    style={{ justifyContent: 'flex-start' }}
                  >
                    <IconBtn
                      title={copied ? 'Copied' : 'Copy message'}
                      onClick={() => void copyMsg()}
                      active={copied}
                    >
                      {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
                    </IconBtn>
                    {onRegenerate && !streaming && (
                      <IconBtn title="Regenerate reply" onClick={() => onRegenerate(msg.id)}>
                        <IconRefresh size={13} />
                      </IconBtn>
                    )}
                    {canReact && onReact && (
                      <ReactionPicker
                        messageId={msg.id}
                        current={msg.reactions?.user}
                        open={pickerOpen}
                        onOpenChange={setPickerOpen}
                        onReact={onReact}
                      />
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        ) : (
        <div
          className={bubbleClass}
          {...pressHandlers}
          style={{
            background: bubbleBg,
            color: bubbleFg,
            border: `1px solid ${bubbleBorder}`,
            borderRadius: 'var(--chat-bubble-radius)',
            padding: 'var(--chat-bubble-pad)',
            fontSize: 'var(--chat-font)',
          }}
        >
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

          <div
            className={`message-body chat-bubble-body${
              isStreamingPartial && !isUser && !isSystem ? ' stream-md' : ''
            }`}
          >
            {displayText ? (
              <>
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
            </div>
          )}
        </div>
        )}
      </div>
      )}
      {showBubble && !isSystem && (
        <ReactionPills
          messageId={msg.id}
          reactions={msg.reactions}
          partnerName={partnerLabel}
          onReact={canReact ? onReact : undefined}
          align={isUser ? 'end' : 'start'}
        />
      )}
    </div>
  )
})

export function MessageFeed({
  messages,
  partialText,
  textBreaks = [],
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
  starters,
  emptySub,
  emptyHints,
  onRegenerate,
  onReact,
  userName,
  partnerName = 'Remedy',
  onAttachMarkup,
  hasOlder = false,
  loadingOlder = false,
  onLoadOlder,
  projectPath = null,
  sessionId = null,
  stickNonce,
  hideWork = false,
  onSelectTurn,
}: MessageFeedProps) {
  const { t, resolved } = useI18n()
  const dateLocale = intlLocale(resolved)
  const defaultStarters = useMemo(() => [
    { label: t('starter.help'), text: t('starter.helpPrompt') },
    { label: t('starter.explore'), text: t('starter.explorePrompt') },
    { label: t('starter.review'), text: t('starter.reviewPrompt') },
    { label: t('starter.plan'), text: t('starter.planPrompt') },
  ], [t])
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
  // The owner's newest message (or a submit that is still in flight — the
  // stickNonce) re-attaches the feed to the floor. Nothing else forces it —
  // tokens, tools, images never do; a session switch floors via the new
  // scroller mount, not via this key.
  const lastUserMsgId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]!
      if (m.role === 'user' && !m.reverted) return m.id
    }
    return null
  }, [messages])
  const {
    setScroller,
    setContent,
    showJump,
    jumpLatest,
    anchorToggle,
  } = useStickToBottom({
    endKeyJumps: true,
    enabled: messages.some((message) => !message.reverted) || streaming,
    followActive: streaming,
    alwaysOfferJump: messages.length > 2 || streaming,
    reattachKey: `${lastUserMsgId ?? ''}:${stickNonce}`,
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
  const turnIds = useMemo(() => turnIdForMessages(visible), [visible])

  // Stable so the memoized live turn does not re-render the reply per parent render.
  const renderLiveReply = useCallback(
    (msg: ChatMessage, reply: string) => (
      <MessageBubble
        msg={msg}
        partial={reply}
        partialThinking=""
        textBreaks={textBreaks}
        toolProcessMode={toolProcessMode}
        isStreamingPartial
        onOpenImage={openLightbox}
        partnerName={partnerName}
        hideWork={hideWork}
        turnId={lastUserMsgId}
        onSelectTurn={onSelectTurn}
      />
    ),
    [toolProcessMode, openLightbox, partnerName, textBreaks, hideWork, lastUserMsgId, onSelectTurn],
  )

  // Unread delta for the jump pill: messages that landed since the user
  // scrolled away (+1 while a reply is still streaming in).
  const detachedAtCountRef = useRef<number | null>(null)
  if (showJump && detachedAtCountRef.current === null) {
    detachedAtCountRef.current = visible.length
  } else if (!showJump && detachedAtCountRef.current !== null) {
    detachedAtCountRef.current = null
  }
  const unread = showJump && detachedAtCountRef.current !== null
    ? Math.max(0, visible.length - detachedAtCountRef.current) + (streaming && partialText ? 1 : 0)
    : 0

  // Window long chats: only mount recent messages (virtualization lite).
  // "Load earlier" reveals one more chunk per click — never the whole transcript.
  const [revealed, setRevealed] = useState(0)
  useEffect(() => {
    setRevealed(0)
  }, [sessionId])
  const windowed = useMemo(() => {
    const start = feedWindowStart(visible.length, revealed)
    return start > 0 ? visible.slice(start) : visible
  }, [visible, revealed])
  const hiddenCount = feedHiddenCount(visible.length, revealed)
  const nextChunk = feedNextChunk(visible.length, revealed)

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
        items.push({ type: 'day', key: dk, label: dayLabel(msg.created_at, Date.now(), dateLocale) })
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
  }, [windowed, dateLocale])

  return (
    <div className="message-feed-shell">
    <div
      ref={setScroller}
      className="message-feed work-host py-2 relative w-full"
      style={{
        position: 'relative',
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
      }}
    >
      <FeedScrollContext.Provider value={anchorToggle}>
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
            <kbd style={{ opacity: 0.85 }}>Shift+Tab</kbd>
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
            onClick={() => setRevealed((r) => r + nextChunk)}
          >
            Load {nextChunk} earlier · {hiddenCount} hidden
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
              onReact={onReact}
              userName={userName}
              partnerName={partnerName}
              sessionId={sessionId}
              hideWork={hideWork}
              turnId={turnIds.get(msg.id) ?? null}
              onSelectTurn={onSelectTurn}
            />
          </Fragment>
        )
      })}

      {/* Live turn: work stays in the strip (or the actions pane when hideWork). */}
      {streaming && hideWork && (
        <div className="chat-working-hint" data-testid="chat-working-hint" role="status">
          <span className="live-stream-dot" aria-hidden />
          <span>Working</span>
        </div>
      )}
      {streaming && (
        <div className="live-stream-dock">
          <LiveTurnView
            partialText={partialText}
            textBreaks={textBreaks}
            partialThinking={partialThinking}
            processSteps={processSteps}
            renderReply={renderLiveReply}
            progress={taskProgress}
            mode={toolProcessMode}
            sessionId={sessionId}
            showWork={!hideWork}
            extras={!hideWork && buildTodos.length ? <BuildTodos items={buildTodos} live /> : undefined}
          />
        </div>
      )}

      {!hideWork && !streaming && todosHaveOpen(buildTodos) && (
        <BuildTodos items={buildTodos} />
      )}

      {!loading && visible.length === 0 && !streaming && (
        <div className="chat-empty-hero">
          <RemedyLogo size={88} variant="auto" title="Remedy" />
          <div className="chat-empty-title">
            {userName?.trim()
              ? t('empty.readyNamed', { name: firstName(userName) })
              : t('empty.ready')}
          </div>
          <div className="chat-empty-sub">
            {emptySub ?? (projectPath ? (
              <>
                {t('empty.withProject')}
                <br />
                <code className="text-[0.7em] break-all">{projectPath}</code>
              </>
            ) : (
              t('empty.noProject')
            ))}
          </div>
          {onQuickPrompt && (
            <div
              className="flex flex-wrap justify-center gap-2 mt-2 max-w-lg"
              role="group"
              aria-label="Starter prompts"
            >
              {(starters ?? defaultStarters).map((s) => (
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
            {emptyHints ?? t('empty.hints')}
          </div>
        </div>
      )}

      <div aria-hidden className="h-px w-full" />
      </div>
      </FeedScrollContext.Provider>

      <ImageLightbox
        src={lightbox?.src ?? null}
        alt={lightbox?.alt}
        onClose={closeLightbox}
        onAttachMarkup={onAttachMarkup}
      />
    </div>
      {showJump && (
        <div className="scroll-latest-dock">
          <button
            type="button"
            className="scroll-latest-fab scroll-latest-pill"
            onClick={jumpLatest}
            title={t('empty.jump')}
            aria-label={
              unread > 0
                ? `${t('empty.jump')}, ${t('empty.jumpNew', { n: String(unread) })}`
                : t('empty.jump')
            }
          >
            {unread > 0 && (
              <span className="scroll-latest-count">{t('empty.jumpNew', { n: String(unread) })}</span>
            )}
            <span>{t('empty.jump')}</span>
            <span aria-hidden>↓</span>
          </button>
        </div>
      )}
    </div>
  )
}
