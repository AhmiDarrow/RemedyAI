/** Skills panel API helpers. */

import { apiFetch, authHeaders, ensureApiToken, getApiBase } from './client'

export type SkillRow = {
  name: string
  description: string
  version: string
  status?: string
  tags?: string[]
  effort_weight?: number
  effort_band?: string | null
  auto_generated?: boolean
  quarantine?: boolean
  success_rate?: number | null
  related?: string[]
  lifecycle?: string | null
  lifecycle_last?: string | null
  path?: string | null
}

export async function listSkills(q = ''): Promise<SkillRow[]> {
  const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
  const list = await apiFetch<SkillRow[]>(`/skills${qs}`)
  return Array.isArray(list) ? list : []
}

export async function getSkillDetail(name: string): Promise<SkillRow & { body?: string; instructions_preview?: string }> {
  return apiFetch(`/skills/${encodeURIComponent(name)}`)
}

export async function setSkillStatus(
  name: string,
  status: string,
  opts?: { force_promote?: boolean; quarantine?: boolean },
): Promise<{ name: string; status: string; quarantine?: boolean }> {
  return apiFetch(`/skills/${encodeURIComponent(name)}/status`, {
    method: 'POST',
    body: JSON.stringify({ status, ...opts }),
  })
}

export async function setSkillQuarantine(
  name: string,
  quarantine: boolean,
): Promise<{ name: string; quarantine: boolean; status: string }> {
  return apiFetch(`/skills/${encodeURIComponent(name)}/quarantine`, {
    method: 'POST',
    body: JSON.stringify({ quarantine }),
  })
}

export async function saveSkillBody(
  name: string,
  body: string,
): Promise<{ name: string; status: string; chars: number }> {
  return apiFetch(`/skills/${encodeURIComponent(name)}/body`, {
    method: 'PUT',
    body: JSON.stringify({ body }),
  })
}

export async function skillFeedback(name: string, success: boolean): Promise<void> {
  await apiFetch(`/skills/${encodeURIComponent(name)}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ success }),
  })
}

/** Download a skill pack ZIP (selected names or all). */
export async function exportSkillsPack(names?: string[]): Promise<void> {
  await ensureApiToken()
  const res = await fetch(`${getApiBase()}/skills/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ names: names?.length ? names : [] }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(
      (err as { detail?: string }).detail || res.statusText || 'Export failed',
    )
  }
  const blob = await res.blob()
  const cd = res.headers.get('content-disposition') || ''
  const m = /filename="?([^";]+)"?/i.exec(cd)
  const filename = m?.[1] || `remedy-skills-pack.zip`
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export async function importSkillsPack(file: File): Promise<{
  imported: number
  names: string[]
  quarantine: boolean
}> {
  await ensureApiToken()
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${getApiBase()}/skills/import`, {
    method: 'POST',
    headers: { ...authHeaders() },
    body: form,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(
      (err as { detail?: string }).detail || res.statusText || 'Import failed',
    )
  }
  return res.json()
}
