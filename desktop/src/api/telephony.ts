/** Phone line status — terms, offered transports, owner's pick. */

import { apiFetch } from './client'

export interface TelephonyLine {
  name: string
  title: string
  summary: string
  cost: string
  catch: string
  ready: boolean
  achievable: boolean
  missing: string[]
  action: string
  standalone: boolean
}

export interface TelephonyStatus {
  terms: {
    agreed: boolean
    stale: boolean
    version: number
    current_version: number
    ask: string
  }
  chosen: string
  offer: string
  lines: TelephonyLine[]
  real_line: boolean
  phase: number
  message: string
}

export async function getTelephonyStatus(): Promise<TelephonyStatus> {
  return apiFetch<TelephonyStatus>('/telephony/status')
}

export async function acceptTelephonyTerms(
  accept = true,
): Promise<{ ok: boolean; agreed: boolean }> {
  return apiFetch('/telephony/terms', {
    method: 'POST',
    body: JSON.stringify({ accept }),
  })
}

export async function chooseTelephonyLine(
  name: string,
): Promise<{ ok: boolean; chosen?: string; error?: string; available?: string[] }> {
  return apiFetch('/telephony/choose', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}
