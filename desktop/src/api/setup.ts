/** First-run setup session — shared draft for conversation and Traditional setup. */

import { apiFetch } from './client'

export interface SetupProgress {
  phase?: string
  message?: string
  bytes_done?: number
  bytes_total?: number
  error?: string | null
  cancellable?: boolean
}

export interface SetupMindFailure {
  reason?: string
  detail?: string
  at?: string
}

export interface SetupQuestion {
  id: string
  title: string
  prompt: string
  group?: string
  skippable?: boolean
}

export interface SetupUpdateOffer {
  available: boolean
  id?: string
  title?: string
  body?: string
  primary?: string
  secondary?: string
}

export interface SetupSession {
  version?: number
  mode: 'conversation' | 'traditional'
  status: string
  onboarding_pending?: boolean
  mind_ready?: boolean
  mind_failure?: SetupMindFailure | null
  mind_progress?: SetupProgress | null
  fallback_suggested?: boolean
  draft: Record<string, unknown>
  skipped?: string[]
  secrets_set?: string[]
  setup_session_id?: string
  traditional_available?: boolean
  questions?: SetupQuestion[]
  summary?: string
  update_offer?: SetupUpdateOffer
}

export async function getSetupSession(): Promise<SetupSession> {
  return apiFetch<SetupSession>('/setup')
}

export async function updateSetupSession(patch: {
  mode?: 'conversation' | 'traditional'
  draft?: Record<string, unknown>
  skipped?: string[]
  action?: 'begin' | 'skip_all' | 'confirm' | 'update_offer_go' | 'update_offer_later'
  setup_session_id?: string
  onboarding_pending?: boolean
}): Promise<SetupSession> {
  return apiFetch<SetupSession>('/setup', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export async function retryMindSetup(): Promise<{ ok?: boolean; error?: string }> {
  return apiFetch('/setup/mind/retry', { method: 'POST', body: '{}' })
}

export function mindFailureMessage(failure?: SetupMindFailure | null): string {
  if (!failure) return ''
  const detail = (failure.detail || '').trim()
  switch (failure.reason) {
    case 'low_ram':
      return detail || 'This computer does not have enough memory to start the mind comfortably.'
    case 'verify':
      return detail || 'The mind download could not be verified. You can retry, or continue with Traditional setup.'
    case 'download':
      return detail || 'The mind could not be downloaded. You can retry, or continue with Traditional setup.'
    default:
      return detail || 'The mind could not start. You can retry, or continue with Traditional setup.'
  }
}

export const UPDATE_OFFER_FALLBACK: SetupUpdateOffer = {
  available: false,
  id: 'major-changes-2026-11',
  title: 'House rules',
  body:
    "Remedy has changed in some big ways: a new built-in mind, smarter token saving, local muscle sharing, and talking with your other bots. It's worth a few minutes to go over the house rules together.",
  primary: "Let's go",
  secondary: 'Not now',
}
