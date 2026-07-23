import { getApiBase } from './client'

export interface AttachmentMeta {
  id: string
  name: string
  path: string
  mime: string
  size: number
  is_image: boolean
  is_text: boolean
  /** Local object URL for image previews (client-only). */
  previewUrl?: string
}

export async function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<AttachmentMeta> {
  const form = new FormData()
  form.append('file', file, file.name)

  const res = await fetch(`${getApiBase()}/sessions/${sessionId}/attachments`, {
    method: 'POST',
    body: form,
    // Do NOT set Content-Type — browser sets multipart boundary.
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

  const meta = (await res.json()) as AttachmentMeta
  if (meta.is_image || file.type.startsWith('image/')) {
    meta.previewUrl = URL.createObjectURL(file)
  }
  return meta
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
