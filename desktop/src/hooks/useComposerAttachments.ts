/**
 * Composer attachment rail: state, File upload, native drop payloads, drag chrome.
 */

import { useCallback, useRef, useState } from 'react'
import {
  pendingMetaFromPayload,
  uploadAttachment,
  uploadDroppedPayload,
  type AttachmentMeta,
  type DroppedFilePayload,
} from '../api/attachments'

const MAX_FILES = 12

const nameSizeKey = (name: string, size: number) =>
  `${String(name || '').toLowerCase()}|${Number(size) || 0}`

function normalizePayload(
  raw: DroppedFilePayload | Record<string, unknown>,
): DroppedFilePayload {
  const r = raw as Record<string, unknown>
  const filename = String(r.filename ?? r.fileName ?? 'file')
  const content_type = String(
    r.content_type ?? r.contentType ?? 'application/octet-stream',
  )
  const data_base64 = String(r.data_base64 ?? r.dataBase64 ?? '')
  const size = Number(r.size ?? 0)
  return { filename, content_type, data_base64, size }
}

export function useComposerAttachments(opts: {
  ensureSessionId: () => Promise<string | null>
  disabled?: boolean
  onError?: (msg: string) => void
}) {
  const { ensureSessionId, disabled, onError } = opts
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [attachNotice, setAttachNotice] = useState('')

  const attachmentsRef = useRef<AttachmentMeta[]>([])
  attachmentsRef.current = attachments
  const inflightDropKeysRef = useRef<Set<string>>(new Set())
  const dragDepth = useRef(0)
  const dragClearTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearDragOver = useCallback(() => {
    dragDepth.current = 0
    setDragOver(false)
    if (dragClearTimer.current) {
      clearTimeout(dragClearTimer.current)
      dragClearTimer.current = null
    }
  }, [])

  const armDragOver = useCallback(() => {
    setDragOver(true)
    if (dragClearTimer.current) clearTimeout(dragClearTimer.current)
    dragClearTimer.current = setTimeout(() => {
      dragDepth.current = 0
      setDragOver(false)
      dragClearTimer.current = null
    }, 2500)
  }, [])

  const attachNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashAttached = useCallback((n: number) => {
    if (n <= 0) return
    setAttachNotice(
      n === 1
        ? '1 file attached to this message'
        : `${n} files attached to this message`,
    )
    if (attachNoticeTimer.current) clearTimeout(attachNoticeTimer.current)
    attachNoticeTimer.current = setTimeout(() => {
      attachNoticeTimer.current = null
      setAttachNotice('')
    }, 2500)
  }, [])

  const clearAttachments = useCallback(() => {
    for (const a of attachmentsRef.current) {
      if (a.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(a.previewUrl)
    }
    setAttachments([])
    setUploadError('')
    setAttachNotice('')
    inflightDropKeysRef.current.clear()
  }, [])

  const removeAttachment = useCallback((idx: number) => {
    setAttachments((prev) => {
      const copy = [...prev]
      const [gone] = copy.splice(idx, 1)
      if (gone) {
        inflightDropKeysRef.current.delete(nameSizeKey(gone.name, gone.size))
        if (gone.previewUrl?.startsWith('blob:')) {
          URL.revokeObjectURL(gone.previewUrl)
        }
      }
      return copy
    })
  }, [])

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files || []).filter(Boolean)
      if (!list.length || disabled) return
      setUploadError('')
      setAttachNotice('')
      const sid = await ensureSessionId()
      if (!sid) {
        const msg = 'Could not create a session for the upload.'
        setUploadError(msg)
        onError?.(msg)
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
            next.push(await uploadAttachment(sid, file))
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
    [disabled, ensureSessionId, flashAttached, onError],
  )

  const addNativePayloads = useCallback(
    async (payloads: DroppedFilePayload[]) => {
      if (!payloads.length || disabled) return
      const normalized = payloads
        .map(normalizePayload)
        .filter((p) => p.data_base64)
      const unique = normalized.filter((p) => {
        const key = nameSizeKey(p.filename, p.size)
        if (inflightDropKeysRef.current.has(key)) return false
        const already = attachmentsRef.current.some(
          (a) => nameSizeKey(a.name, a.size) === key,
        )
        return !already
      })
      if (!unique.length) {
        const onRail = normalized.some((p) =>
          attachmentsRef.current.some(
            (a) =>
              nameSizeKey(a.name, a.size) === nameSizeKey(p.filename, p.size),
          ),
        )
        if (onRail) {
          setUploadError(
            'Already on this message — remove the chip to re-attach the same file.',
          )
          window.setTimeout(() => setUploadError(''), 3500)
        }
        return
      }
      setUploadError('')
      setAttachNotice('')
      clearDragOver()
      const room = MAX_FILES - attachmentsRef.current.length
      if (room <= 0) {
        setUploadError(`Max ${MAX_FILES} attachments per message.`)
        return
      }
      const batch = unique.slice(0, room)
      const batchKeys = batch.map((p) => nameSizeKey(p.filename, p.size))
      for (const k of batchKeys) inflightDropKeysRef.current.add(k)
      const optimistic = batch.map(pendingMetaFromPayload)
      setAttachments((prev) => [...prev, ...optimistic])
      flashAttached(batch.length)
      setUploading(true)
      const releaseInflight = () => {
        for (const k of batchKeys) inflightDropKeysRef.current.delete(k)
      }
      const sid = await ensureSessionId()
      if (!sid) {
        setUploadError('Could not create a session for the upload.')
        releaseInflight()
        setAttachments((prev) =>
          prev.filter(
            (a) =>
              !a.id.startsWith('pending-')
              || !batch.some((b) => b.filename === a.name),
          ),
        )
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
            const pendingNames = new Set(batch.map((b) => b.filename))
            const withoutOptimistic = prev.filter(
              (a) => !(a.id.startsWith('pending-') && pendingNames.has(a.name)),
            )
            const merged = [...withoutOptimistic, ...uploaded]
            const seen = new Set<string>()
            return merged.filter((a) => {
              const k = nameSizeKey(a.name, a.size)
              if (seen.has(k)) return false
              seen.add(k)
              return true
            })
          })
        } else {
          const pendingNames = new Set(batch.map((b) => b.filename))
          setAttachments((prev) =>
            prev.filter(
              (a) => !(a.id.startsWith('pending-') && pendingNames.has(a.name)),
            ),
          )
          setUploadError(
            (prev) => prev || 'Upload failed — files not stored for the agent.',
          )
        }
      } finally {
        releaseInflight()
        setUploading(false)
      }
    },
    [disabled, ensureSessionId, flashAttached, clearDragOver],
  )

  return {
    attachments,
    setAttachments,
    attachmentsRef,
    inflightDropKeysRef,
    dragOver,
    setDragOver,
    uploading,
    setUploading,
    uploadError,
    setUploadError,
    attachNotice,
    setAttachNotice,
    clearAttachments,
    removeAttachment,
    addFiles,
    addNativePayloads,
    clearDragOver,
    armDragOver,
    dragDepth,
    flashAttached,
    MAX_FILES,
  }
}
