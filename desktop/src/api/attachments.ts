import { getApiBase } from './client'
import { isTauri, tauriInvoke, tauriListen } from './tauri'

export interface AttachmentMeta {
  id: string
  name: string
  path: string
  mime: string
  size: number
  is_image: boolean
  is_text: boolean
  /** Local object URL / data URL for image previews (client-only). */
  previewUrl?: string
}

export interface DroppedFilePayload {
  filename: string
  content_type: string
  data_base64: string
  size: number
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string') {
        reject(new Error('Failed to read file'))
        return
      }
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error || new Error('FileReader failed'))
    reader.readAsDataURL(file)
  })
}

async function postAttachmentJson(
  sessionId: string,
  filename: string,
  content_type: string | undefined,
  data_base64: string,
): Promise<AttachmentMeta> {
  const res = await fetch(`${getApiBase()}/sessions/${sessionId}/attachments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename,
      content_type: content_type || undefined,
      data_base64,
    }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const msg =
      (body as { detail?: string; error?: string })?.detail
      || (body as { error?: string })?.error
      || res.statusText
      || `Upload failed (${res.status})`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }

  return (await res.json()) as AttachmentMeta
}

/**
 * Upload via JSON + base64 (works in frozen PyInstaller sidecars without python-multipart).
 */
export async function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<AttachmentMeta> {
  const data_base64 = await fileToBase64(file)
  const meta = await postAttachmentJson(
    sessionId,
    file.name,
    file.type || undefined,
    data_base64,
  )
  // Keep the name the user picked (📎 / paste), not a server rename.
  meta.name = file.name
  if (meta.is_image || file.type.startsWith('image/')) {
    meta.previewUrl = URL.createObjectURL(file)
  }
  return meta
}

/** Upload a payload produced by the Tauri native drop reader. */
export async function uploadDroppedPayload(
  sessionId: string,
  payload: DroppedFilePayload,
): Promise<AttachmentMeta> {
  const meta = await postAttachmentJson(
    sessionId,
    payload.filename,
    payload.content_type,
    payload.data_base64,
  )
  // Always show the original OS filename — never server-side unique suffixes (_1/_4).
  meta.name = payload.filename
  if (meta.is_image || payload.content_type.startsWith('image/')) {
    meta.previewUrl = `data:${payload.content_type};base64,${payload.data_base64}`
  }
  return meta
}

/** Optimistic chip before server upload completes (from native drop payloads). */
export function pendingMetaFromPayload(payload: DroppedFilePayload): AttachmentMeta {
  const isImage = payload.content_type.startsWith('image/')
  return {
    id: `pending-${payload.filename}-${payload.size}`,
    name: payload.filename,
    path: `(uploading) ${payload.filename}`,
    mime: payload.content_type,
    size: payload.size,
    is_image: isImage,
    is_text: !isImage && (
      payload.content_type.startsWith('text/')
      || /\.(txt|md|py|ts|js|json|csv|log|toml|ya?ml)$/i.test(payload.filename)
    ),
    previewUrl: isImage
      ? `data:${payload.content_type};base64,${payload.data_base64}`
      : undefined,
  }
}

/** Read OS-dropped paths via Rust (fallback if ready event not used). */
export async function readDroppedFilePaths(
  paths: string[],
): Promise<DroppedFilePayload[]> {
  if (!isTauri()) {
    throw new Error('Native drop only available in desktop app')
  }
  return tauriInvoke<DroppedFilePayload[]>('read_dropped_files', { paths })
}

/**
 * Drain files queued by the last native OS drop.
 * Primary path on Windows — event delivery to the webview is unreliable.
 */
export async function takePendingFileDrops(): Promise<DroppedFilePayload[]> {
  if (!isTauri()) return []
  try {
    const items = await tauriInvoke<DroppedFilePayload[]>('take_pending_file_drops')
    return Array.isArray(items) ? items : []
  } catch {
    return []
  }
}

export type FileDragPhase = 'enter' | 'over' | 'leave' | 'drop'

/**
 * Subscribe to native window drag-drop.
 * Prefers `file-drop-ready` (payloads already read in Rust) for reliable UI chips.
 */
export async function listenNativeFileDrop(
  onReady: (payloads: DroppedFilePayload[]) => void,
  onPhase?: (phase: FileDragPhase) => void,
  onError?: (message: string) => void,
  onPathsFallback?: (paths: string[]) => void,
): Promise<() => void> {
  if (!isTauri()) return () => {}

  const unsubs: Array<() => void> = []

  unsubs.push(
    await tauriListen('file-drop-ready', (payload) => {
      if (Array.isArray(payload) && payload.length) {
        onReady(payload as DroppedFilePayload[])
      }
    }),
  )

  unsubs.push(
    await tauriListen('file-drop-error', (payload) => {
      const msg =
        payload && typeof payload === 'object' && 'message' in (payload as object)
          ? String((payload as { message: unknown }).message)
          : 'Failed to read dropped files'
      onError?.(msg)
    }),
  )

  unsubs.push(
    await tauriListen('file-drop', (payload) => {
      // Fallback path only if ready event is not delivered
      if (Array.isArray(payload) && onPathsFallback) {
        onPathsFallback(payload.filter((p): p is string => typeof p === 'string'))
      }
    }),
  )

  unsubs.push(
    await tauriListen('file-drag', (payload) => {
      const p = payload as { phase?: string }
      if (p?.phase === 'enter' || p?.phase === 'over' || p?.phase === 'leave') {
        onPhase?.(p.phase)
      }
    }),
  )

  return () => {
    for (const u of unsubs) u()
  }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
