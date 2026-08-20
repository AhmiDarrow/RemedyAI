/** GroveChat — the full Remedy conversation inside Grove.
 *
 * Grove used to send into an invisible session: a talkbar with no feed, no
 * attachments, no images. This panel is the real thing — the same
 * MessageFeed Studio uses (images render, lightbox, markup-attach) plus a
 * warm composer with the full attachment rail: pick files, paste a
 * screenshot, drop a receipt, point at a bug. Full ability, Grove tone.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageFeed } from '../components/MessageFeed'
import type { SendAttachment } from '../components/Composer'
import { useComposerAttachments } from '../hooks/useComposerAttachments'
import { pickAttachFiles } from '../api/attachments'
import type { ChatMessage } from '../types'

/**
 * Grove's own starter chips — life tone, never Studio's workbench set
 * (explore/review/plan belong to Studio sessions). A chip whose text ends
 * in a space or ellipsis prefills the composer instead of sending, so
 * "Plant a goal" rides the same "I want to…" goal-planting path.
 */
const GROVE_STARTERS = [
  {
    label: 'What can you help with?',
    text: 'What kinds of things can you help me with, day to day?',
  },
  {
    label: 'Plant a goal',
    text: 'I want to ',
  },
  {
    label: 'Plan my day',
    text: 'Help me plan my day — ask me what’s on my plate.',
  },
  {
    label: 'Check on my goals',
    text: 'How are my goals doing? Anything that needs me today?',
  },
]

export interface GroveChatProps {
  messages: ChatMessage[]
  partialText: string
  streaming: boolean
  loading: boolean
  userName: string
  partnerName: string
  serverReady: boolean
  placeholder: string
  /** Send with full attachments — same signature as Studio's flow. */
  onSend: (text: string, attachments?: SendAttachment[]) => Promise<void> | void
  onStop: () => void
  /** Ensure the surface's session exists (goal room binds its own). */
  ensureSessionId: () => Promise<string | null>
  sessionKey?: string | null
  /** Optional pre-send hook: return true when the text was consumed
   * (e.g. Grove home plants "I want to…" as a goal instead of chatting). */
  onSpecialSend?: (text: string) => Promise<boolean>
  /** Empty-state starter chips — Grove home tone by default; goal rooms pass their own. */
  starters?: { label: string; text: string }[]
  /** Mic passthrough (Grove's voice loop owns recording state). */
  micSupported?: boolean
  recording?: boolean
  onMic?: () => void
}

export function GroveChat({
  messages,
  partialText,
  streaming,
  loading,
  userName,
  partnerName,
  serverReady,
  placeholder,
  onSend,
  onStop,
  ensureSessionId,
  sessionKey,
  onSpecialSend,
  starters,
  micSupported,
  recording,
  onMic,
}: GroveChatProps) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  // Synchronous re-entry guard: two Enter presses in one frame both see the
  // stale `busy` state; the ref closes that window (no double-send).
  const busyRef = useRef(false)
  const [attachError, setAttachError] = useState('')

  const {
    attachments,
    dragOver,
    uploading,
    uploadError,
    attachNotice,
    setAttachNotice,
    addFiles,
    removeAttachment,
    clearAttachments,
    armDragOver,
    clearDragOver,
    dragDepth,
  } = useComposerAttachments({
    ensureSessionId,
    sessionKey,
    disabled: !serverReady,
    onError: setAttachError,
  })

  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  // Auto-grow the composer up to a few lines, then scroll internally.
  const autoGrow = useCallback(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
  }, [])

  // Collapse back to one line when cleared (after send).
  useEffect(() => {
    if (!draft && taRef.current) taRef.current.style.height = 'auto'
  }, [draft])

  const onDragEnter = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      dragDepth.current += 1
      if (e.dataTransfer.types.includes('Files')) armDragOver()
    },
    [armDragOver, dragDepth],
  )
  const onDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.dataTransfer.types.includes('Files')) {
        e.dataTransfer.dropEffect = 'copy'
        armDragOver()
      }
    },
    [armDragOver],
  )
  const onDragLeave = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const root = rootRef.current
      const related = e.relatedTarget as Node | null
      if (!root || !related || !root.contains(related)) {
        dragDepth.current = 0
        clearDragOver()
        return
      }
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) clearDragOver()
    },
    [clearDragOver, dragDepth],
  )
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      dragDepth.current = 0
      clearDragOver()
      if (e.dataTransfer.files?.length) void addFiles(e.dataTransfer.files)
    },
    [addFiles, clearDragOver],
  )
  const onPaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (!items) return
      const files: File[] = []
      for (const item of Array.from(items)) {
        if (item.kind === 'file') {
          const f = item.getAsFile()
          if (f) files.push(f)
        }
      }
      if (files.length) {
        e.preventDefault()
        void addFiles(files)
      }
    },
    [addFiles],
  )

  const attachFromMarkup = useCallback(
    async (file: File) => {
      await addFiles([file])
    },
    [addFiles],
  )

  const pickFiles = useCallback(async () => {
    // Tauri-native picker first; browser file input as the dev fallback.
    try {
      const payloads = await pickAttachFiles()
      if (payloads.length) {
        const files = payloads.map((p) => {
          const bytes = Uint8Array.from(atob(p.data_base64), (c) => c.charCodeAt(0))
          return new File([bytes], p.filename, { type: p.content_type })
        })
        await addFiles(files)
        return
      }
    } catch {
      /* fall through to browser input */
    }
    fileInputRef.current?.click()
  }, [addFiles])

  const send = useCallback(async () => {
    const text = draft.trim()
    if ((!text && attachments.length === 0) || busy || busyRef.current || !serverReady) return
    busyRef.current = true
    setBusy(true)
    try {
      if (text && onSpecialSend && attachments.length === 0) {
        const consumed = await onSpecialSend(text)
        if (consumed) {
          setDraft('')
          return
        }
      }
      const payload: SendAttachment[] = attachments.map((a) => ({
        path: a.path,
        name: a.name,
        mime: a.mime,
        size: a.size,
        is_image: a.is_image,
        is_text: a.is_text,
      }))
      await onSend(text, payload.length ? payload : undefined)
      setDraft('')
      clearAttachments()
      setAttachError('')
      setAttachNotice('')
    } catch (e) {
      setAttachError(e instanceof Error ? e.message : 'Send failed — try again.')
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }, [
    draft,
    attachments,
    busy,
    serverReady,
    onSpecialSend,
    onSend,
    clearAttachments,
    setAttachNotice,
  ])

  /** Starter chips: prefill-style texts hand the pen over; the rest send
   *  through the same special-send path as typed messages (goal planting). */
  const quickPrompt = useCallback(
    (text: string) => {
      if (/[\s…]$/.test(text)) {
        setDraft(text)
        requestAnimationFrame(() => {
          taRef.current?.focus()
          autoGrow()
        })
        return
      }
      void (async () => {
        if (busy || busyRef.current || !serverReady) return
        busyRef.current = true
        setBusy(true)
        try {
          if (onSpecialSend) {
            const consumed = await onSpecialSend(text)
            if (consumed) return
          }
          await onSend(text)
        } catch (e) {
          setAttachError(e instanceof Error ? e.message : 'Send failed — try again.')
        } finally {
          busyRef.current = false
          setBusy(false)
        }
      })()
    },
    [busy, serverReady, onSpecialSend, onSend, autoGrow],
  )

  return (
    <div
      ref={rootRef}
      className={`grove-chat${dragOver ? ' dragover' : ''}`}
      data-testid="grove-chat"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="grove-chat-feed">
        <MessageFeed
          messages={messages}
          partialText={partialText}
          streaming={streaming}
          loading={loading}
          userName={userName}
          partnerName={partnerName}
          onAttachMarkup={attachFromMarkup}
          onQuickPrompt={quickPrompt}
          starters={starters ?? GROVE_STARTERS}
          emptySub={
            <>
              Tell me what you’re carrying — a goal, an errand, a receipt,
              a bug. You can drop a file right here.
            </>
          }
          emptyHints={
            <>
              <code>Enter</code> send · <code>Shift+Enter</code> new line ·{' '}
              <code>📎</code> attach{micSupported ? <> · <code>🎙</code> speak</> : null}
              {' '}· while she works, <code>Enter</code> steers
            </>
          }
          sessionId={sessionKey}
        />
      </div>

      {(attachments.length > 0 || uploading || uploadError || attachError || attachNotice) && (
        <div className="grove-attachrail" data-testid="grove-attachrail">
          {attachments.map((a, idx) => (
            <span key={a.path || a.name} className={`grove-attachchip${a.is_image ? ' img' : ''}`}>
              {a.is_image ? '🖼' : '📄'} {a.name}
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                onClick={() => removeAttachment(idx)}
              >
                ×
              </button>
            </span>
          ))}
          {uploading && <span className="grove-attachnote">uploading…</span>}
          {(uploadError || attachError) && (
            <span className="grove-attachnote err">{uploadError || attachError}</span>
          )}
          {attachNotice && <span className="grove-attachnote">{attachNotice}</span>}
        </div>
      )}

      <form
        className="grove-talkbar chatbar"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <button
          type="button"
          className="grove-attachbtn"
          onClick={() => void pickFiles()}
          disabled={!serverReady}
          aria-label="Attach a file, receipt, or image"
          title="Attach a file, receipt, or image — or paste / drop it here"
        >
          📎
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            const files = e.currentTarget.files
            if (files?.length) void addFiles(files)
            e.currentTarget.value = ''
          }}
        />
        <textarea
          ref={taRef}
          rows={1}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            autoGrow()
          }}
          onPaste={onPaste}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter (and IME composition) makes a newline.
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              void send()
            }
          }}
          aria-label="Talk to Remedy"
          placeholder={
            !serverReady
              ? 'Connecting…'
              : recording
                ? 'Listening…'
                : streaming
                  ? 'Steer me — say it and I change course'
                  : placeholder
          }
          disabled={!serverReady}
        />
        {micSupported && onMic && (
          <button
            type="button"
            className={`grove-micbtn${recording ? ' rec' : ''}`}
            onClick={onMic}
            disabled={!serverReady}
            aria-pressed={recording}
            aria-label={recording ? 'Stop and send what you said' : 'Speak instead of typing'}
          >
            🎙
          </button>
        )}
        {streaming ? (
          <>
            <button
              type="button"
              className="grove-mic stop"
              onClick={onStop}
              title="Pause"
              aria-label="Pause"
            >
              ⏸
            </button>
            <button
              type="submit"
              className="grove-mic steer"
              title="Steer — send now and she changes course"
              aria-label="Steer: send now and change course"
            >
              ↑
            </button>
          </>
        ) : (
          <button type="submit" className="grove-mic" title="Send" aria-label="Send">
            ↑
          </button>
        )}
      </form>
      {dragOver && <div className="grove-dropveil">Drop it here — I’ll take a look</div>}
    </div>
  )
}
