/**
 * Composer attachment rail state + upload helpers.
 */

import { useCallback, useState } from 'react'
import { uploadAttachment, type AttachmentMeta } from '../api/attachments'

export function useComposerAttachments(opts: {
  ensureSessionId: () => Promise<string | null>
  onError?: (msg: string) => void
}) {
  const { ensureSessionId, onError } = opts
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [attachNotice, setAttachNotice] = useState('')

  const clearAttachments = useCallback(() => {
    setAttachments([])
    setUploadError('')
    setAttachNotice('')
  }, [])

  const removeAttachment = useCallback((path: string) => {
    setAttachments((prev) => prev.filter((a) => a.path !== path))
  }, [])

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files || [])
      if (!list.length) return
      setUploading(true)
      setUploadError('')
      try {
        const sid = await ensureSessionId()
        if (!sid) {
          const msg = 'No session for upload'
          setUploadError(msg)
          onError?.(msg)
          return
        }
        const next: AttachmentMeta[] = []
        for (const f of list) {
          const meta = await uploadAttachment(sid, f)
          if (meta) next.push(meta as AttachmentMeta)
        }
        if (next.length) {
          setAttachments((prev) => [...prev, ...next])
          setAttachNotice(
            next.length === 1
              ? `Attached ${next[0]?.name || 'file'}`
              : `Attached ${next.length} files`,
          )
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        setUploadError(msg)
        onError?.(msg)
      } finally {
        setUploading(false)
      }
    },
    [ensureSessionId, onError],
  )

  return {
    attachments,
    setAttachments,
    dragOver,
    setDragOver,
    uploading,
    uploadError,
    setUploadError,
    attachNotice,
    setAttachNotice,
    clearAttachments,
    removeAttachment,
    addFiles,
  }
}
