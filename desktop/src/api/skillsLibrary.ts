/** Skills Library (remote catalog) API. */

import { apiFetch } from './client'

export type LibrarySkill = {
  id: string
  name: string
  description: string
  version: string
  author: string
  tags: string[]
  download_url: string
  size_bytes: number
  checksum: string
  security_flags: string[]
  status: string
  rating?: number
  installs?: number
}

export type LibraryCatalog = {
  version: string
  generated_at: string
  repository?: string
  skills: LibrarySkill[]
  source?: string
}

export async function fetchLibraryCatalog(refresh = false): Promise<LibraryCatalog> {
  const qs = refresh ? '?refresh=true' : ''
  return apiFetch(`/skills/library/catalog${qs}`)
}

export async function searchLibrary(q: string): Promise<{
  total: number
  results: LibrarySkill[]
  source?: string
}> {
  const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
  return apiFetch(`/skills/library/search${qs}`)
}

export async function installLibrarySkill(
  skillId: string,
  opts?: { force?: boolean; version?: string },
): Promise<{
  status: string
  names: string[]
  version: string
  quarantine: boolean
  message?: string
}> {
  return apiFetch('/skills/library/install', {
    method: 'POST',
    body: JSON.stringify({
      skill_id: skillId,
      force: opts?.force ?? false,
      version: opts?.version ?? null,
    }),
  })
}

export async function checkLibraryUpdates(): Promise<{
  updates: {
    skill_id: string
    name: string
    current_version: string
    available_version: string
  }[]
}> {
  return apiFetch('/skills/library/updates')
}
