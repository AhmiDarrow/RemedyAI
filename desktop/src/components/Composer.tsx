import { useState, useRef, useCallback, useEffect } from 'react'
import { searchFiles } from '../api/messages'
import {
  uploadAttachment,
  formatBytes,
  type AttachmentMeta,
} from '../api/attachments'

export interface AgentDef {
  name: string
  description: string
}

export type SendAttachment = {
  path: string
  name?: string
  mime?: string
  size?: number
  is_image?: boolean
  is_text?: boolean
}

interface ComposerProps {
  onSend: (text: string, attachments?: SendAttachment[]) => void
  onStop: () => void
  onCommand: (command: string) => void
  streaming: boolean
  disabled: boolean
  planMode?: boolean
  agents?: AgentDef[]
  /** When set, loads text into the composer (edit-and-resend flow). */
  editDraft?: string | null
  onEditDraftConsumed?: () => void
  sessionId?: string | null
  /** Create a session if needed before upload. */
  ensureSession?: () => Promise<string | null>
}

type SuggestionItem = { label: string; value: string; icon: string; type: 'file' | 'agent' }

const MAX_FILES = 12

export function Composer({
  onSend,
  onStop,
  onCommand,
  streaming,
  disabled,
  planMode,
  agents = [],
  editDraft,
  onEditDraftConsumed,
  sessionId,
  ensureSession,
}: ComposerProps) {
  const [input, setInput] = useState('')
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [suggestionIdx, setSuggestionIdx] = useState(0)
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const submittingRef = useRef(false)
  const dragDepth = useRef(0)

  useEffect(() => {
    if (editDraft != null && editDraft !== '') {
      setInput(editDraft)
      onEditDraftConsumed?.()
      requestAnimationFrame(() => {
        const el = textareaRef.current
        if (el) {
          el.focus()
          el.selectionStart = el.selectionEnd = el.value.length
        }
      })
    }
  }, [editDraft, onEditDraftConsumed])

  // Revoke preview URLs on unmount / clear
  useEffect(() => {
    return () => {
      for (const a of attachments) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on unmount
  }, [])

  const detectAtQuery = useCallback((text: string, cursorPos: number) => {
    const before = text.slice(0, cursorPos)
    const match = before.match(/@(\S*)$/)
    return match ? match[1] : null
  }, [])

  const handleSuggestionSelect = useCallback(
    (item: SuggestionItem) => {
      const cursorPos = textareaRef.current?.selectionStart ?? input.length
      const before = input.slice(0, cursorPos)
      const after = input.slice(cursorPos)
      const atIdx = before.lastIndexOf('@')
      const newInput = before.slice(0, atIdx) + item.value + ' ' + after
      setInput(newInput)
      setShowSuggestions(false)
      textareaRef.current?.focus()
    },
    [input],
  )

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files).filter(Boolean)
      if (!list.length || disabled || streaming) return

      setUploadError('')
      let sid = sessionId
      if (!sid && ensureSession) {
        sid = await ensureSession()
      }
      if (!sid) {
        setUploadError('Create a session first (or send a message).')
        return
      }

      const room = MAX_FILES - attachments.length
      if (room <= 0) {
        setUploadError(`Max ${MAX_FILES} attachments per message.`)
        return
      }

      setUploading(true)
      try {
        const next: AttachmentMeta[] = []
        for (const file of list.slice(0, room)) {
          try {
            const meta = await uploadAttachment(sid, file)
            next.push(meta)
          } catch (e: unknown) {
            setUploadError(e instanceof Error ? e.message : 'Upload failed')
          }
        }
        if (next.length) {
          setAttachments((prev) => [...prev, ...next])
        }
      } finally {
        setUploading(false)
      }
    },
    [attachments.length, disabled, streaming, sessionId, ensureSession],
  )

  const removeAttachment = useCallback((idx: number) => {
    setAttachments((prev) => {
      const copy = [...prev]
      const [gone] = copy.splice(idx, 1)
      if (gone?.previewUrl) URL.revokeObjectURL(gone.previewUrl)
      return copy
    })
  }, [])

  const handleSubmit = useCallback(() => {
    const text = input.trim()
    if ((!text && attachments.length === 0) || streaming || disabled || uploading) return
    if (submittingRef.current) return
    submittingRef.current = true
    try {
      if (text.startsWith('/') && attachments.length === 0) {
        onCommand(text)
      } else {
        const payload = attachments.map((a) => ({
          path: a.path,
          name: a.name,
          mime: a.mime,
          size: a.size,
          is_image: a.is_image,
          is_text: a.is_text,
        }))
        onSend(text, payload.length ? payload : undefined)
      }
      setInput('')
      for (const a of attachments) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
      }
      setAttachments([])
      setUploadError('')
    } finally {
      requestAnimationFrame(() => {
        submittingRef.current = false
      })
    }
  }, [input, attachments, onSend, onCommand, streaming, disabled, uploading])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (showSuggestions) {
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          setSuggestionIdx((i) => (i + 1) % suggestions.length)
          return
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault()
          setSuggestionIdx((i) => (i - 1 + suggestions.length) % suggestions.length)
          return
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          if (suggestions[suggestionIdx]) {
            handleSuggestionSelect(suggestions[suggestionIdx])
          }
          return
        }
        if (e.key === 'Escape') {
          setShowSuggestions(false)
          return
        }
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [showSuggestions, suggestions, suggestionIdx, handleSuggestionSelect, handleSubmit],
  )

  const handleChange = useCallback(
    (text: string) => {
      setInput(text)
      const cursorPos = textareaRef.current?.selectionStart ?? text.length
      const q = detectAtQuery(text, cursorPos)

      if (q !== null && q.length >= 1) {
        clearTimeout(suggestTimer.current ?? undefined)
        suggestTimer.current = setTimeout(async () => {
          const items: SuggestionItem[] = []

          const matchedAgents = agents
            .filter((a) => a.name.toLowerCase().includes(q.toLowerCase()))
            .slice(0, 4)
            .map((a) => ({
              label: a.name,
              value: `@${a.name}`,
              icon: '@',
              type: 'agent' as const,
            }))
          items.push(...matchedAgents)

          if (q.length >= 2) {
            try {
              const r = await searchFiles(q)
              for (const f of r.results.slice(0, 6)) {
                items.push({
                  label: f.name,
                  value: f.path,
                  icon: f.is_dir ? '\u25B6' : '\u25CF',
                  type: 'file' as const,
                })
              }
            } catch {
              // ignore
            }
          }

          if (items.length > 0) {
            setSuggestions(items)
            setSuggestionIdx(0)
            setShowSuggestions(true)
          } else {
            setShowSuggestions(false)
          }
        }, 120)
      } else {
        setShowSuggestions(false)
      }
    },
    [agents, detectAtQuery],
  )

  const onDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragDepth.current += 1
    if (e.dataTransfer.types.includes('Files')) setDragOver(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragOver(false)
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.dataTransfer.types.includes('Files')) {
      e.dataTransfer.dropEffect = 'copy'
    }
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      dragDepth.current = 0
      setDragOver(false)
      if (e.dataTransfer.files?.length) {
        void addFiles(e.dataTransfer.files)
      }
    },
    [addFiles],
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

  useEffect(() => {
    textareaRef.current?.focus()
  }, [streaming])

  const canSend =
    !disabled && !streaming && !uploading && (Boolean(input.trim()) || attachments.length > 0)

  return (
    <div
      className="p-3 border-t flex flex-col relative"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {dragOver && (
        <div
          className="absolute inset-2 z-20 rounded-lg flex items-center justify-center pointer-events-none"
          style={{
            border: '2px dashed var(--accent)',
            background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
            color: 'var(--accent)',
          }}
        >
          <div className="text-sm font-medium">Drop files or images to attach</div>
        </div>
      )}

      {suggestions.length > 0 && showSuggestions && (
        <div
          className="mb-1 rounded-md border text-xs max-h-40 overflow-y-auto"
          style={{
            background: 'var(--bg-primary)',
            borderColor: 'var(--border)',
          }}
        >
          {suggestions.map((s, i) => (
            <button
              key={`${s.type}-${s.value}`}
              className="flex items-center gap-2 w-full text-left px-3 py-1.5"
              style={{
                background: i === suggestionIdx ? 'var(--bg-tertiary)' : 'transparent',
                color: 'var(--text-primary)',
              }}
              onMouseDown={(e) => {
                e.preventDefault()
                handleSuggestionSelect(s)
              }}
            >
              <span style={{ color: s.type === 'agent' ? 'var(--accent)' : 'var(--text-muted)', width: 16, textAlign: 'center' }}>
                {s.icon}
              </span>
              <span className="truncate">{s.label}</span>
              <span className="ml-auto text-[0.65rem]" style={{ color: 'var(--text-muted)' }}>
                {s.type}
              </span>
            </button>
          ))}
        </div>
      )}

      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.map((a, i) => (
            <div
              key={`${a.path}-${i}`}
              className="flex items-center gap-2 rounded-md px-2 py-1 text-xs max-w-full"
              style={{
                background: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                color: 'var(--text-secondary)',
              }}
            >
              {a.previewUrl ? (
                <img
                  src={a.previewUrl}
                  alt={a.name}
                  className="w-8 h-8 rounded object-cover flex-shrink-0"
                />
              ) : (
                <span className="flex-shrink-0 opacity-70">{a.is_text ? '📄' : '📎'}</span>
              )}
              <div className="min-w-0">
                <div className="truncate font-medium" style={{ color: 'var(--text-primary)' }}>
                  {a.name}
                </div>
                <div style={{ color: 'var(--text-muted)' }}>
                  {formatBytes(a.size)}
                  {a.mime ? ` · ${a.mime}` : ''}
                </div>
              </div>
              <button
                type="button"
                className="ml-1 px-1 rounded opacity-70 hover:opacity-100"
                style={{ color: 'var(--error)' }}
                title="Remove"
                onClick={() => removeAttachment(i)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {uploadError && (
        <div className="mb-1 text-xs" style={{ color: 'var(--error)' }}>
          {uploadError}
        </div>
      )}

      <div className="flex items-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) void addFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <button
          type="button"
          title="Attach files"
          disabled={disabled || streaming || uploading}
          onClick={() => fileInputRef.current?.click()}
          className="px-2.5 py-2 rounded-md text-sm flex-shrink-0"
          style={{
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
            opacity: disabled || streaming || uploading ? 0.5 : 1,
          }}
        >
          📎
        </button>

        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          placeholder={
            planMode
              ? 'Plan mode — describe what to do (no tools executed)'
              : 'Message, /command, @file…  ·  drop or paste files & images'
          }
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-md px-3 py-2 text-sm outline-none transition-colors"
          style={{
            background: 'var(--bg-primary)',
            border: `1px solid ${dragOver ? 'var(--accent)' : 'var(--border)'}`,
            color: 'var(--text-primary)',
            maxHeight: 160,
          }}
          onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
          onBlur={(e) => {
            if (!dragOver) e.currentTarget.style.borderColor = 'var(--border)'
          }}
        />

        {streaming ? (
          <button
            onClick={onStop}
            className="px-4 py-2 rounded-md text-sm font-medium transition-colors"
            style={{ background: 'var(--error)', color: '#fff' }}
          >
            Stop
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!canSend}
            className="px-4 py-2 rounded-md text-sm font-medium transition-colors"
            style={{
              background: !canSend ? 'var(--bg-tertiary)' : 'var(--accent)',
              color: !canSend ? 'var(--text-muted)' : '#fff',
              cursor: !canSend ? 'not-allowed' : 'pointer',
            }}
          >
            {uploading ? '…' : 'Send'}
          </button>
        )}
      </div>
      <div className="mt-1 text-[0.65rem]" style={{ color: 'var(--text-muted)' }}>
        Drag & drop or paste images/files · max {MAX_FILES} per message
      </div>
    </div>
  )
}
