import {
  useState,
  useRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  forwardRef,
} from 'react'
import { searchFiles, listCommands } from '../api/messages'
import type { CommandDefinition } from '../types'
import {
  uploadAttachment,
  uploadDroppedPayload,
  listenNativeFileDrop,
  takePendingFileDrops,
  pendingMetaFromPayload,
  formatBytes,
  type AttachmentMeta,
  type DroppedFilePayload,
} from '../api/attachments'
import { isTauri } from '../api/tauri'
import {
  getVisionStatus,
  chatModelSupportsVision,
  type VisionStatus,
} from '../api/vision'
import { IconPaperclip, IconSend, IconStop } from './icons'
import { ImageLightbox } from './ImageLightbox'

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

export type ComposerQueuedItem = {
  id: string
  text: string
  mode: 'after' | 'interrupt'
}

interface ComposerProps {
  onSend: (
    text: string,
    attachments?: SendAttachment[],
    opts?: { mode?: 'after' | 'interrupt' },
  ) => void
  onStop: () => void
  onCommand: (command: string) => void
  streaming: boolean
  disabled: boolean
  /** Pending prompts while a turn is in flight. */
  queue?: ComposerQueuedItem[]
  onCancelQueued?: (id: string) => void
  onClearQueue?: () => void
  onPromoteQueued?: (id: string) => void
  onUpdateQueued?: (id: string, patch: { text?: string; mode?: 'after' | 'interrupt' }) => void
  planMode?: boolean
  /** Shift+Tab toggles Plan ↔ Build (composer-focused). */
  onTogglePlanMode?: () => void
  agents?: AgentDef[]
  /**
   * Prefill for edit-and-resend. Use a new `key` every time so the same text
   * re-applies, and keep the value until the parent clears it (survives remounts).
   */
  editDraft?: { text: string; key: number } | null
  sessionId?: string | null
  /** Create a session if needed before upload. */
  ensureSession?: () => Promise<string | null>
  /** Optional preloaded slash commands (falls back to API). */
  slashCommands?: CommandDefinition[]
  /** Current chat provider/model — used for visual-decoder banner. */
  llmProvider?: string
  llmModel?: string
  /** Open Settings (optionally scrolled to vision later). */
  onOpenSettings?: () => void
}

/** Imperative API so the image viewer can attach markup to the prompt rail. */
export type ComposerHandle = {
  addFiles: (files: FileList | File[]) => Promise<void>
  focus: () => void
}

type SuggestionItem = {
  label: string
  value: string
  icon: string
  type: 'file' | 'agent' | 'command'
  description?: string
}

const FALLBACK_COMMANDS: CommandDefinition[] = [
  { name: '/help', description: 'List commands', aliases: [], arguments: null },
  { name: '/new', description: 'New session', aliases: [], arguments: null },
  { name: '/compact', description: 'Compact conversation', aliases: [], arguments: null },
  { name: '/remember', description: 'Save a memory', aliases: [], arguments: null },
  { name: '/thinking', description: 'Toggle thinking visibility', aliases: [], arguments: null },
  { name: '/export', description: 'Export session as .txt', aliases: [], arguments: null },
  {
    name: '/import-session',
    description: 'Import session from .txt / .md',
    aliases: ['/session-import'],
    arguments: 'path',
  },
]

const MAX_FILES = 12
const PROMPT_HISTORY_KEY = 'remedy.composer.promptHistory'
const PROMPT_HISTORY_MAX = 80
/** Grow with content until this height, then scroll (px). */
const COMPOSER_MAX_HEIGHT = 280
const COMPOSER_MIN_HEIGHT = 44

function resizeComposerTextarea(el: HTMLTextAreaElement | null) {
  if (!el) return
  // Reset so scrollHeight reflects content (not the previous fixed height).
  el.style.height = 'auto'
  el.style.overflowY = 'hidden'
  const next = Math.min(
    Math.max(el.scrollHeight, COMPOSER_MIN_HEIGHT),
    COMPOSER_MAX_HEIGHT,
  )
  el.style.height = `${next}px`
  el.style.overflowY = el.scrollHeight > COMPOSER_MAX_HEIGHT ? 'auto' : 'hidden'
}

function loadPromptHistory(): string[] {
  try {
    const raw = localStorage.getItem(PROMPT_HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((x): x is string => typeof x === 'string' && x.trim().length > 0).slice(0, PROMPT_HISTORY_MAX)
  } catch {
    return []
  }
}

function savePromptHistory(entries: string[]) {
  try {
    localStorage.setItem(PROMPT_HISTORY_KEY, JSON.stringify(entries.slice(0, PROMPT_HISTORY_MAX)))
  } catch {
    /* quota / private mode */
  }
}

export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  {
    onSend,
    onStop,
    onCommand,
    streaming,
    disabled,
    queue = [],
    onCancelQueued,
    onClearQueue,
    onPromoteQueued,
    onUpdateQueued,
    planMode,
    onTogglePlanMode,
    agents = [],
    editDraft,
    sessionId,
    ensureSession,
    slashCommands: slashCommandsProp,
    llmProvider = '',
    llmModel = '',
    onOpenSettings,
  },
  ref,
) {
  const [input, setInput] = useState('')
  const [previewImage, setPreviewImage] = useState<{ src: string; alt?: string } | null>(
    null,
  )
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [visionStatus, setVisionStatus] = useState<VisionStatus | null>(null)
  const [suggestionIdx, setSuggestionIdx] = useState(0)
  const [slashCommands, setSlashCommands] = useState<CommandDefinition[]>(
    slashCommandsProp?.length ? slashCommandsProp : FALLBACK_COMMANDS,
  )

  useEffect(() => {
    if (slashCommandsProp?.length) {
      setSlashCommands(slashCommandsProp)
      return
    }
    let cancelled = false
    listCommands()
      .then((r) => {
        if (cancelled) return
        const cmds = r?.commands
        if (Array.isArray(cmds) && cmds.length) setSlashCommands(cmds)
      })
      .catch(() => {
        /* keep fallback */
      })
    return () => {
      cancelled = true
    }
  }, [slashCommandsProp])
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [attachNotice, setAttachNotice] = useState('')
  const hasImageAttachments = attachments.some((a) => a.is_image)
  const modelHasVision = chatModelSupportsVision(llmProvider, llmModel)

  useEffect(() => {
    if (!hasImageAttachments) return
    let cancelled = false
    void getVisionStatus()
      .then((s) => {
        if (!cancelled) setVisionStatus(s)
      })
      .catch(() => {
        if (!cancelled) setVisionStatus(null)
      })
    return () => {
      cancelled = true
    }
  }, [hasImageAttachments, attachments.length])

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const attachRailRef = useRef<HTMLDivElement>(null)
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const submittingRef = useRef(false)
  const dragDepth = useRef(0)
  const attachmentsRef = useRef<AttachmentMeta[]>([])
  attachmentsRef.current = attachments
  /** Dedupe keys for drop/event/poll triple-fire (same file was attaching 3×). */
  const seenDropKeysRef = useRef<Set<string>>(new Set())
  /** Last applied edit key — re-apply when parent issues a new edit, including remount. */
  const lastEditKeyRef = useRef<number | null>(null)
  /** Shell-style prompt history: newest first in storage; index navigates with ↑/↓. */
  const promptHistoryRef = useRef<string[]>(loadPromptHistory())
  const historyIndexRef = useRef<number>(-1) // -1 = drafting current (not browsing history)
  const draftBeforeHistoryRef = useRef<string>('')

  const dropKey = (p: DroppedFilePayload) =>
    `${p.filename}|${p.size}|${(p.data_base64 || '').slice(0, 48)}`

  const flashAttached = useCallback((n: number) => {
    if (n <= 0) return
    setAttachNotice(n === 1 ? '1 file attached to this message' : `${n} files attached to this message`)
    window.setTimeout(() => setAttachNotice(''), 2500)
    requestAnimationFrame(() => {
      attachRailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }, [])

  // Load full original prompt into the bar when the user clicks Edit.
  // Parent keeps editDraft until send/session change so remounts don't blank it.
  useEffect(() => {
    if (!editDraft) return
    if (lastEditKeyRef.current === editDraft.key) return
    lastEditKeyRef.current = editDraft.key
    const text = editDraft.text ?? ''
    setInput(text)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (!el) return
      el.focus()
      const len = el.value.length
      el.selectionStart = el.selectionEnd = len
      // Grow textarea to fit the restored prompt.
      el.style.height = 'auto'
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`
    })
  }, [editDraft])

  // Revoke blob preview URLs on unmount
  useEffect(() => {
    return () => {
      for (const a of attachmentsRef.current) {
        if (a.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(a.previewUrl)
      }
    }
  }, [])

  const detectAtQuery = useCallback((text: string, cursorPos: number) => {
    const before = text.slice(0, cursorPos)
    const match = before.match(/@(\S*)$/)
    return match ? match[1] : null
  }, [])

  /** Slash menu only at start of input (or sole line beginning with /). */
  const detectSlashQuery = useCallback((text: string, cursorPos: number) => {
    const before = text.slice(0, cursorPos)
    if (!before.startsWith('/')) return null
    if (before.includes('\n')) return null
    // Full line is the command draft
    if (before.includes(' ') && !before.endsWith(' ')) {
      // typing arguments — hide menu
      return null
    }
    return before.slice(1) // without leading /
  }, [])

  const handleSuggestionSelect = useCallback(
    (item: SuggestionItem) => {
      const cursorPos = textareaRef.current?.selectionStart ?? input.length
      const before = input.slice(0, cursorPos)
      const after = input.slice(cursorPos)
      if (item.type === 'command') {
        setInput(item.value + (item.value.endsWith(' ') ? '' : ' '))
        setShowSuggestions(false)
        textareaRef.current?.focus()
        return
      }
      const atIdx = before.lastIndexOf('@')
      const newInput = before.slice(0, atIdx) + item.value + ' ' + after
      setInput(newInput)
      setShowSuggestions(false)
      textareaRef.current?.focus()
    },
    [input],
  )

  const resolveSession = useCallback(async (): Promise<string | null> => {
    if (sessionId) return sessionId
    if (ensureSession) return ensureSession()
    return null
  }, [sessionId, ensureSession])

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files).filter(Boolean)
      // Markup attach from image viewer is allowed while streaming (queued send).
      if (!list.length || disabled) return

      setUploadError('')
      setAttachNotice('')
      const sid = await resolveSession()
      if (!sid) {
        setUploadError('Could not create a session for the upload.')
        return
      }

      const room = MAX_FILES - attachmentsRef.current.length
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
          flashAttached(next.length)
        }
      } finally {
        setUploading(false)
      }
    },
    [disabled, resolveSession, flashAttached],
  )

  useImperativeHandle(
    ref,
    () => ({
      addFiles,
      focus: () => textareaRef.current?.focus(),
    }),
    [addFiles],
  )

  /**
   * Native drop payloads → chips + upload (same as 📎).
   * Dedupes poll + event + path fallback so one drop ≠ three chips.
   */
  const addNativePayloads = useCallback(
    async (payloads: DroppedFilePayload[]) => {
      if (!payloads.length || disabled || streaming) return

      // Drop anything already handled (or already attached by name+size).
      const unique = payloads.filter((p) => {
        const key = dropKey(p)
        if (seenDropKeysRef.current.has(key)) return false
        const already = attachmentsRef.current.some(
          (a) => a.name === p.filename && a.size === p.size,
        )
        if (already) {
          seenDropKeysRef.current.add(key)
          return false
        }
        seenDropKeysRef.current.add(key)
        return true
      })
      if (!unique.length) return

      setUploadError('')
      setAttachNotice('')
      setDragOver(false)

      const room = MAX_FILES - attachmentsRef.current.length
      if (room <= 0) {
        setUploadError(`Max ${MAX_FILES} attachments per message.`)
        return
      }
      const batch = unique.slice(0, room)

      // Instant UI chips (before server round-trip).
      const optimistic = batch.map(pendingMetaFromPayload)
      setAttachments((prev) => [...prev, ...optimistic])
      flashAttached(batch.length)
      setUploading(true)

      const sid = await resolveSession()
      if (!sid) {
        setUploadError('Could not create a session for the upload.')
        setUploading(false)
        return
      }

      try {
        const uploaded: AttachmentMeta[] = []
        for (const p of batch) {
          try {
            uploaded.push(await uploadDroppedPayload(sid, p))
          } catch (e: unknown) {
            setUploadError(e instanceof Error ? e.message : 'Upload failed')
          }
        }
        if (uploaded.length) {
          setAttachments((prev) => {
            // Remove only the optimistic chips for this batch, keep others.
            const pendingNames = new Set(batch.map((b) => b.filename))
            const withoutOptimistic = prev.filter(
              (a) => !(a.id.startsWith('pending-') && pendingNames.has(a.name)),
            )
            // Also drop accidental duplicates of the same name+size from races.
            const merged = [...withoutOptimistic, ...uploaded]
            const seen = new Set<string>()
            return merged.filter((a) => {
              const k = `${a.name}|${a.size}`
              if (seen.has(k)) return false
              seen.add(k)
              return true
            })
          })
        } else {
          setUploadError((prev) => prev || 'Upload failed — files not stored for the agent.')
        }
      } finally {
        setUploading(false)
      }
    },
    [disabled, streaming, resolveSession, flashAttached],
  )

  // Primary: poll Rust pending queue. Secondary: events only for drag highlight.
  // (Previously poll + ready event + path fallback all added the same file → 3 chips.)
  useEffect(() => {
    let cancelled = false
    let unlisten: (() => void) | undefined
    let inFlight = false

    const drainPending = async () => {
      if (cancelled || disabled || streaming || inFlight) return
      inFlight = true
      try {
        const pending = await takePendingFileDrops()
        if (!cancelled && pending.length > 0) {
          setDragOver(false)
          await addNativePayloads(pending)
        }
      } catch (e: unknown) {
        if (!cancelled && isTauri()) {
          console.warn('[remedy] take_pending_file_drops failed', e)
        }
      } finally {
        inFlight = false
      }
    }

    const pollId = window.setInterval(() => {
      void drainPending()
    }, 200)

    // Events: highlight only — content comes from the pending queue to avoid triple attach.
    void listenNativeFileDrop(
      () => {
        // ready event: also drain once (queue may already be empty if poll won — that's ok)
        void drainPending()
      },
      (phase) => {
        if (phase === 'enter' || phase === 'over') setDragOver(true)
        if (phase === 'leave') setDragOver(false)
      },
      (msg) => {
        setUploadError(msg)
        setDragOver(false)
      },
      // No path fallback — it re-read and triple-attached the same files.
      undefined,
    ).then((fn) => {
      if (!cancelled) unlisten = fn
      else fn()
    })

    void drainPending()

    return () => {
      cancelled = true
      window.clearInterval(pollId)
      unlisten?.()
    }
  }, [addNativePayloads, disabled, streaming])

  const removeAttachment = useCallback((idx: number) => {
    setAttachments((prev) => {
      const copy = [...prev]
      const [gone] = copy.splice(idx, 1)
      if (gone?.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(gone.previewUrl)
      return copy
    })
  }, [])

  const applyHistoryEntry = useCallback((text: string) => {
    setInput(text)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (!el) return
      resizeComposerTextarea(el)
      // Cursor at end so user can edit / send immediately
      const len = el.value.length
      el.selectionStart = el.selectionEnd = len
    })
  }, [])

  // Auto-grow (wrap) then scroll once max height is hit.
  useEffect(() => {
    resizeComposerTextarea(textareaRef.current)
  }, [input])

  const pushPromptHistory = useCallback((text: string) => {
    const t = text.trim()
    if (!t) return
    const prev = promptHistoryRef.current
    // Dedupe consecutive duplicates; move match to front
    const next = [t, ...prev.filter((x) => x !== t)].slice(0, PROMPT_HISTORY_MAX)
    promptHistoryRef.current = next
    savePromptHistory(next)
    historyIndexRef.current = -1
    draftBeforeHistoryRef.current = ''
  }, [])

  const handleSubmit = useCallback(
    (mode: 'after' | 'interrupt' = 'after') => {
      const text = input.trim()
      if ((!text && attachments.length === 0) || disabled || uploading) return
      // Commands still require a quiet moment (no concurrent turn side-effects).
      if (text.startsWith('/') && attachments.length === 0 && streaming) return
      if (submittingRef.current) return
      submittingRef.current = true
      try {
        if (text) pushPromptHistory(text)
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
          // Streaming: queue (after) or interrupt depending on mode.
          onSend(text, payload.length ? payload : undefined, streaming ? { mode } : undefined)
        }
        setInput('')
        historyIndexRef.current = -1
        draftBeforeHistoryRef.current = ''
        for (const a of attachments) {
          if (a.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(a.previewUrl)
        }
        setAttachments([])
        setUploadError('')
        setAttachNotice('')
        seenDropKeysRef.current.clear()
      } finally {
        requestAnimationFrame(() => {
          submittingRef.current = false
        })
      }
    },
    [input, attachments, onSend, onCommand, streaming, disabled, uploading, pushPromptHistory],
  )

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

      // Prompt history (shell-style): ↑ previous, ↓ next — when not in multi-line mid-edit
      const el = textareaRef.current
      const hist = promptHistoryRef.current
      if (el && hist.length > 0 && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const atStart = el.selectionStart === 0 && el.selectionEnd === 0
        const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length
        const empty = el.value.length === 0
        const singleLine = !el.value.includes('\n')

        if (e.key === 'ArrowUp' && (empty || (atStart && singleLine) || (atStart && historyIndexRef.current >= 0))) {
          e.preventDefault()
          if (historyIndexRef.current === -1) {
            draftBeforeHistoryRef.current = input
          }
          const nextIdx = Math.min(historyIndexRef.current + 1, hist.length - 1)
          historyIndexRef.current = nextIdx
          applyHistoryEntry(hist[nextIdx] ?? '')
          return
        }
        if (e.key === 'ArrowDown' && historyIndexRef.current >= 0 && (atEnd || singleLine || empty)) {
          e.preventDefault()
          const nextIdx = historyIndexRef.current - 1
          if (nextIdx < 0) {
            historyIndexRef.current = -1
            applyHistoryEntry(draftBeforeHistoryRef.current)
            draftBeforeHistoryRef.current = ''
          } else {
            historyIndexRef.current = nextIdx
            applyHistoryEntry(hist[nextIdx] ?? '')
          }
          return
        }
      }

      // Shift+Tab: Plan ↔ Build (requested; works while typing / streaming).
      if (e.key === 'Tab' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        onTogglePlanMode?.()
        return
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        // Ctrl/Cmd+Enter while streaming → interrupt; plain Enter → queue after.
        if (streaming && (e.ctrlKey || e.metaKey)) {
          handleSubmit('interrupt')
        } else {
          handleSubmit(streaming ? 'after' : 'after')
        }
      }
    },
    [
      showSuggestions,
      suggestions,
      suggestionIdx,
      handleSuggestionSelect,
      handleSubmit,
      input,
      applyHistoryEntry,
      onTogglePlanMode,
      streaming,
    ],
  )

  const handleChange = useCallback(
    (text: string) => {
      setInput(text)
      // User typed while browsing history → leave history mode; draft becomes current
      if (historyIndexRef.current >= 0) {
        historyIndexRef.current = -1
        draftBeforeHistoryRef.current = ''
      }
      const cursorPos = textareaRef.current?.selectionStart ?? text.length
      const slashQ = detectSlashQuery(text, cursorPos)
      if (slashQ !== null) {
        clearTimeout(suggestTimer.current ?? undefined)
        const ql = slashQ.toLowerCase()
        const items: SuggestionItem[] = slashCommands
          .filter((c) => {
            const name = (c.name || '').replace(/^\//, '').toLowerCase()
            const full = (c.name || '').toLowerCase()
            return !ql || name.startsWith(ql) || full.includes(ql) || (c.description || '').toLowerCase().includes(ql)
          })
          .slice(0, 10)
          .map((c) => {
            const name = c.name.startsWith('/') ? c.name : `/${c.name}`
            return {
              label: name,
              value: name,
              icon: '/',
              type: 'command' as const,
              description: c.description || '',
            }
          })
        if (items.length) {
          setSuggestions(items)
          setSuggestionIdx(0)
          setShowSuggestions(true)
        } else {
          setShowSuggestions(false)
        }
        return
      }

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
    [agents, detectAtQuery, detectSlashQuery, slashCommands],
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
      // HTML5 FileList — works in browser; often empty in Tauri/WebView2 for OS drops.
      if (e.dataTransfer.files?.length) {
        void addFiles(e.dataTransfer.files)
        return
      }
      // Some WebViews put path-like items in items / types only — native handler covers OS.
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
    !disabled &&
    !uploading &&
    (Boolean(input.trim()) || attachments.length > 0) &&
    // Slash commands still wait until the current turn ends.
    !(streaming && input.trim().startsWith('/'))

  return (
    <div
      className="p-3 border-t flex flex-col relative"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {queue.length > 0 && (
        <div
          className="mb-2 rounded-lg border px-2 py-1.5 space-y-1"
          style={{
            borderColor: 'var(--border)',
            background: 'var(--bg-primary)',
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <span
              className="text-[10px] font-semibold uppercase tracking-wide"
              style={{ color: 'var(--text-muted)' }}
            >
              Queue · {queue.length}
            </span>
            {onClearQueue && (
              <button
                type="button"
                className="text-[10px] px-1.5 py-0.5 rounded"
                style={{ color: 'var(--error)' }}
                onClick={() => onClearQueue()}
              >
                Clear all
              </button>
            )}
          </div>
          {queue.map((q, idx) => (
            <div
              key={q.id}
              className="flex items-start gap-1.5 text-xs rounded px-1.5 py-1"
              style={{ background: 'var(--bg-secondary)' }}
            >
              <span style={{ color: 'var(--text-muted)' }} className="shrink-0 pt-0.5">
                {idx + 1}.
              </span>
              <div className="flex-1 min-w-0">
                <div className="truncate" style={{ color: 'var(--text-primary)' }} title={q.text}>
                  {q.text || '(attachments)'}
                </div>
                <div className="flex flex-wrap gap-1 mt-0.5">
                  <button
                    type="button"
                    className="px-1.5 py-0.5 rounded text-[10px]"
                    style={{
                      background:
                        q.mode === 'after' ? 'var(--accent)' : 'var(--bg-tertiary)',
                      color: q.mode === 'after' ? '#fff' : 'var(--text-secondary)',
                    }}
                    title="Send after current turn finishes"
                    onClick={() => onUpdateQueued?.(q.id, { mode: 'after' })}
                  >
                    After
                  </button>
                  <button
                    type="button"
                    className="px-1.5 py-0.5 rounded text-[10px]"
                    style={{
                      background:
                        q.mode === 'interrupt' ? 'var(--warning)' : 'var(--bg-tertiary)',
                      color: q.mode === 'interrupt' ? '#111' : 'var(--text-secondary)',
                    }}
                    title="Interrupt current turn and send now"
                    onClick={() => onPromoteQueued?.(q.id)}
                  >
                    Interrupt
                  </button>
                  <button
                    type="button"
                    className="px-1.5 py-0.5 rounded text-[10px]"
                    style={{ color: 'var(--error)' }}
                    onClick={() => onCancelQueued?.(q.id)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

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

      {hasImageAttachments &&
        (!modelHasVision || (visionStatus?.force_decode && visionStatus?.ready)) && (
        <div
          className="mb-2 px-2.5 py-1.5 rounded-md text-[11px] leading-snug flex items-start justify-between gap-2"
          style={{
            background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-primary))',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
        >
          <div>
            {visionStatus?.ready && (!modelHasVision || visionStatus.force_decode) ? (
              <>
                <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Decoded locally</span>
                {' · '}
                {visionStatus.force_decode && modelHasVision
                  ? `Prefer-local is on — image brief via ${visionStatus.model?.name || 'Qwen2.5-VL 3B'} (saves provider vision tokens).`
                  : `This chat model is text-only; images go through ${visionStatus.model?.name || 'Qwen2.5-VL 3B'} on this PC.`}
              </>
            ) : (
              <>
                This chat model may not see images.
                {visionStatus?.installed
                  ? ' Turn on local vision in Settings — starts with Remedy when enabled.'
                  : ' Install local vision in Settings (one-time Qwen2.5-VL 3B download).'}
              </>
            )}
          </div>
          {onOpenSettings && !visionStatus?.ready ? (
            <button
              type="button"
              className="shrink-0 px-2 py-0.5 rounded text-[10px] font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
              onClick={() => onOpenSettings()}
            >
              Settings
            </button>
          ) : null}
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
              <span
                style={{
                  color:
                    s.type === 'agent' || s.type === 'command'
                      ? 'var(--accent)'
                      : 'var(--text-muted)',
                  width: 16,
                  textAlign: 'center',
                }}
              >
                {s.icon}
              </span>
              <span className="truncate min-w-0">
                {s.label}
                {s.description ? (
                  <span className="ml-1.5" style={{ color: 'var(--text-muted)' }}>
                    {s.description}
                  </span>
                ) : null}
              </span>
              <span className="ml-auto text-[0.65rem] flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
                {s.type}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Same attachment rail for 📎 pick, paste, and drag-drop */}
      {(attachments.length > 0 || uploading || attachNotice) && (
        <div
          ref={attachRailRef}
          className="mb-2 rounded-lg px-3 py-2"
          style={{
            background: 'var(--bg-primary)',
            border: `1px solid ${attachments.length ? 'var(--accent)' : 'var(--border)'}`,
          }}
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
              {uploading && !attachments.length
                ? 'Attaching…'
                : `Attached to this message (${attachments.length})`}
            </div>
            {attachments.length > 0 && !streaming && (
              <button
                type="button"
                className="text-[0.65rem] px-1.5 py-0.5 rounded"
                style={{ color: 'var(--text-muted)' }}
                onClick={() => {
                  for (const a of attachments) {
                    if (a.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(a.previewUrl)
                  }
                  setAttachments([])
                  setAttachNotice('')
                  seenDropKeysRef.current.clear()
                }}
              >
                Clear all
              </button>
            )}
          </div>
          {attachNotice && (
            <div className="text-xs mb-2 font-medium" style={{ color: 'var(--accent)' }}>
              {attachNotice}
            </div>
          )}
          {uploading && (
            <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
              Uploading file(s)…
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {attachments.map((a, i) => (
              <div
                key={`${a.path}-${i}`}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs max-w-[240px]"
                style={{
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
                title={a.path}
              >
                {a.previewUrl ? (
                  <button
                    type="button"
                    className="p-0 border-0 bg-transparent cursor-zoom-in flex-shrink-0"
                    title="Open image viewer / markup"
                    onClick={() =>
                      setPreviewImage({ src: a.previewUrl!, alt: a.name })
                    }
                  >
                    <img
                      src={a.previewUrl}
                      alt={a.name}
                      className="w-10 h-10 rounded object-cover"
                    />
                  </button>
                ) : (
                  <span
                    className="w-10 h-10 rounded flex items-center justify-center flex-shrink-0 text-base"
                    style={{ background: 'var(--bg-tertiary)' }}
                  >
                    {a.is_text ? '📄' : '📎'}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium" style={{ color: 'var(--text-primary)' }}>
                    {a.name}
                  </div>
                  <div className="truncate" style={{ color: 'var(--text-muted)' }}>
                    {formatBytes(a.size)}
                    {a.mime ? ` · ${a.mime.split('/').pop()}` : ''}
                  </div>
                </div>
                {!streaming && (
                  <button
                    type="button"
                    className="ml-0.5 px-1.5 py-0.5 rounded text-sm font-bold"
                    style={{ color: 'var(--error)' }}
                    title="Remove attachment"
                    onClick={() => removeAttachment(i)}
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {uploadError && (
        <div
          className="mb-2 px-2 py-1.5 rounded text-xs"
          style={{
            color: 'var(--error)',
            background: 'var(--error-bg, rgba(239,68,68,0.08))',
            border: '1px solid var(--error)',
          }}
        >
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
          aria-label="Attach files"
          disabled={disabled || streaming || uploading}
          onClick={() => fileInputRef.current?.click()}
          className="relative flex items-center justify-center rounded-xl flex-shrink-0"
          style={{
            width: 40,
            height: 40,
            background: attachments.length ? 'var(--accent)' : 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            color: attachments.length ? '#fff' : 'var(--text-secondary)',
            opacity: disabled || streaming || uploading ? 0.5 : 1,
          }}
        >
          <IconPaperclip size={16} />
          {attachments.length > 0 && (
            <span
              className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 rounded-full text-[0.6rem] font-bold flex items-center justify-center"
              style={{ background: 'var(--bg-primary)', color: 'var(--accent)', border: '1px solid var(--accent)' }}
            >
              {attachments.length}
            </span>
          )}
        </button>

        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          placeholder={
            planMode
              ? 'Plan mode — explore & plan (Shift+Tab → Build)'
              : streaming
                ? 'Send to queue (Enter) · Ctrl+Enter interrupt…'
                : attachments.length
                  ? 'Message (optional)…'
                  : 'Message, /command, @file… (Shift+Tab Plan/Build)'
          }
          disabled={disabled}
          title={
            streaming
              ? 'Enter queues after this turn. Ctrl+Enter interrupts and sends now.'
              : planMode
                ? 'Plan mode: planning tools only. Shift+Tab switches to Build.'
                : undefined
          }
          rows={1}
          className="composer-input flex-1 resize-none rounded-xl px-3 py-2.5 text-sm outline-none transition-colors"
          style={{
            background: 'var(--bg-primary)',
            border: `1px solid ${dragOver || attachments.length ? 'var(--accent)' : 'var(--border)'}`,
            color: 'var(--text-primary)',
            minHeight: COMPOSER_MIN_HEIGHT,
            maxHeight: COMPOSER_MAX_HEIGHT,
            lineHeight: '1.45',
            overflowY: 'hidden',
            wordBreak: 'break-word',
            whiteSpace: 'pre-wrap',
          }}
        />

        <button
          type="button"
          onClick={() => handleSubmit(streaming ? 'after' : 'after')}
          onContextMenu={(e) => {
            if (!streaming || !canSend) return
            e.preventDefault()
            handleSubmit('interrupt')
          }}
          disabled={!canSend}
          title={
            uploading
              ? 'Uploading…'
              : streaming
                ? 'Queue after current turn (right-click or Ctrl+Enter to interrupt)'
                : 'Send'
          }
          aria-label="Send"
          className="flex items-center justify-center rounded-xl flex-shrink-0 transition-colors"
          style={{
            width: 40,
            height: 40,
            background: !canSend ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: !canSend ? 'var(--text-muted)' : '#fff',
            cursor: !canSend ? 'not-allowed' : 'pointer',
            border: '1px solid var(--border)',
          }}
        >
          <IconSend size={16} />
        </button>
        <button
          type="button"
          onClick={onStop}
          disabled={!streaming}
          title={streaming ? 'Stop generation' : 'Stop'}
          aria-label="Stop"
          className="flex items-center justify-center rounded-xl flex-shrink-0 transition-colors"
          style={{
            width: 40,
            height: 40,
            background: streaming ? 'var(--error)' : 'var(--bg-tertiary)',
            color: streaming ? '#fff' : 'var(--text-muted)',
            border: streaming ? 'none' : '1px solid var(--border)',
            cursor: streaming ? 'pointer' : 'not-allowed',
            opacity: streaming ? 1 : 0.55,
          }}
        >
          <IconStop size={14} />
        </button>
      </div>

      <ImageLightbox
        src={previewImage?.src ?? null}
        alt={previewImage?.alt}
        onClose={() => setPreviewImage(null)}
        onAttachMarkup={async (file) => {
          await addFiles([file])
          setPreviewImage(null)
          textareaRef.current?.focus()
        }}
      />
    </div>
  )
})
