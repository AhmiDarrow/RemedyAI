/**
 * One pronoun source for the partner. Every UI string that refers to the
 * agent in the third person goes through these vars, derived from the
 * configured agent_gender (female → she/her/hers, male → he/him/his,
 * neutral / unset / unknown → they/them/theirs).
 *
 * Templates use tokens: {they} {them} {their} {theirs} {themself}, capitalised
 * {They} {Their}, verb agreement {s} ("work{s}" → works / work) and {is} {has}
 * {was}, plus {agent} for the partner's display name.
 */
import { useSyncExternalStore } from 'react'

export type AgentGender = 'female' | 'male' | 'neutral'

export interface PronounVars {
  they: string
  them: string
  their: string
  theirs: string
  themself: string
  They: string
  Their: string
  s: string
  is: string
  has: string
  was: string
  agent: string
}

export function normalizeAgentGender(raw: unknown): AgentGender {
  const g = String(raw ?? '').trim().toLowerCase()
  if (['female', 'f', 'she', 'her', 'woman'].includes(g)) return 'female'
  if (['male', 'm', 'he', 'him', 'man'].includes(g)) return 'male'
  return 'neutral'
}

const cap = (w: string) => w.charAt(0).toUpperCase() + w.slice(1)

export function pronounVars(gender: unknown, agentName?: string): PronounVars {
  const g = normalizeAgentGender(gender)
  const base =
    g === 'female'
      ? { they: 'she', them: 'her', their: 'her', theirs: 'hers', themself: 'herself', plural: false }
      : g === 'male'
        ? { they: 'he', them: 'him', their: 'his', theirs: 'his', themself: 'himself', plural: false }
        : { they: 'they', them: 'them', their: 'their', theirs: 'theirs', themself: 'themself', plural: true }
  return {
    they: base.they,
    them: base.them,
    their: base.their,
    theirs: base.theirs,
    themself: base.themself,
    They: cap(base.they),
    Their: cap(base.their),
    s: base.plural ? '' : 's',
    is: base.plural ? 'are' : 'is',
    has: base.plural ? 'have' : 'has',
    was: base.plural ? 'were' : 'was',
    agent: (agentName || '').trim() || 'Remedy',
  }
}

// ---- live store: settings load/save updates it; t() and hooks re-render ----

interface AgentIdentity {
  gender: AgentGender
  name: string
}

// Matches the server default (agent_identity.DEFAULT_GENDER) until settings load.
let current: AgentIdentity = { gender: 'female', name: 'Remedy' }
const listeners = new Set<() => void>()

export function setAgentIdentity(next: { gender?: unknown; name?: string | null }): void {
  const gender = next.gender === undefined ? current.gender : normalizeAgentGender(next.gender)
  const name = next.name == null ? current.name : next.name.trim() || 'Remedy'
  if (gender === current.gender && name === current.name) return
  current = { gender, name }
  listeners.forEach((fn) => fn())
}

export function getAgentIdentity(): AgentIdentity {
  return current
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function useAgentIdentity(): AgentIdentity {
  return useSyncExternalStore(subscribe, getAgentIdentity, getAgentIdentity)
}

export function usePronouns(): PronounVars {
  const id = useAgentIdentity()
  return pronounVars(id.gender, id.name)
}
