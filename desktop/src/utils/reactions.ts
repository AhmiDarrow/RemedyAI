import type { ChatMessage, MessageReactions } from '../types'

/** The closed reaction set, in picker order (kept in step with the runtime). */
export const REACTIONS = [
  { emoji: '👍', label: 'Thumbs up', meaning: 'Approve or go ahead' },
  { emoji: '👎', label: 'Thumbs down', meaning: 'Reconsider or try again' },
  { emoji: '❤️', label: 'Heart', meaning: 'This landed well' },
  { emoji: '💔', label: 'Broken heart', meaning: 'This disappointed' },
] as const

export type ReactionEmoji = (typeof REACTIONS)[number]['emoji']
export type ReactionSide = 'user' | 'agent'

export function reactionLabel(emoji: string): string {
  return REACTIONS.find((r) => r.emoji === emoji)?.label ?? emoji
}

export function isReaction(emoji: string | undefined | null): emoji is ReactionEmoji {
  return REACTIONS.some((r) => r.emoji === emoji)
}

/** Tapping the reaction you already have removes it; anything else replaces it. */
export function nextReaction(current: string | undefined, picked: string): string {
  return current === picked ? '' : picked
}

/** Which side may react to a message: the owner to Remedy's, Remedy to the owner's. */
export function reactingSide(role: ChatMessage['role']): ReactionSide | null {
  if (role === 'assistant') return 'user'
  if (role === 'user') return 'agent'
  return null
}

/** Immutable update of one side's reaction on one message. */
export function withReaction(
  messages: ChatMessage[],
  id: string,
  side: ReactionSide,
  emoji: string,
): ChatMessage[] {
  let changed = false
  const out = messages.map((m) => {
    if (m.id !== id) return m
    const cur: MessageReactions = { ...(m.reactions || {}) }
    if ((cur[side] || '') === emoji) return m
    if (emoji) cur[side] = emoji
    else delete cur[side]
    changed = true
    return { ...m, reactions: cur.user || cur.agent ? cur : undefined }
  })
  return changed ? out : messages
}

/** Replace a message's reactions with what the server says. */
export function setReactions(
  messages: ChatMessage[],
  id: string,
  reactions: MessageReactions | null | undefined,
): ChatMessage[] {
  const clean: MessageReactions = {}
  if (isReaction(reactions?.user)) clean.user = reactions!.user
  if (isReaction(reactions?.agent)) clean.agent = reactions!.agent
  const next = clean.user || clean.agent ? clean : undefined
  let changed = false
  const out = messages.map((m) => {
    if (m.id !== id) return m
    if ((m.reactions?.user || '') === (next?.user || '') && (m.reactions?.agent || '') === (next?.agent || '')) {
      return m
    }
    changed = true
    return { ...m, reactions: next }
  })
  return changed ? out : messages
}
