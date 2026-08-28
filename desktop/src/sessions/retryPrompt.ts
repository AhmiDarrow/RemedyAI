/**
 * Pure helpers for Stop & retry / promote-queued so multi-provider session
 * binds survive reconnect without depending on React hooks.
 */

export type RetryPromptSnapshot = {
  text: string
  model?: string
  provider?: string
  sid?: string
  attachments?: unknown
  planMode?: boolean
  chatMode?: boolean
}

/** Options passed to send() after Stop & retry. */
export function retrySendOptions(pending: RetryPromptSnapshot): {
  mode: 'after'
  provider?: string
  chatMode?: boolean
} {
  return {
    mode: 'after',
    // Must forward provider — omitting it rebinds the tab to global LLM settings.
    provider: pending.provider,
    chatMode: pending.chatMode,
  }
}

/** Options when promoting a queued item to interrupt the live turn. */
export function promoteQueuedOptions(item: {
  provider?: string
  chatMode?: boolean
}): {
  mode: 'interrupt'
  provider?: string
  chatMode?: boolean
} {
  return {
    mode: 'interrupt',
    provider: item.provider,
    chatMode: item.chatMode,
  }
}

export type BusySendMode = 'after' | 'interrupt' | 'steer'

/**
 * Mid-turn send when /steer did not land. Interrupt only if the owner asked
 * (Ctrl+Enter). A failed steer (nudge cap, 4xx, network) queues after — it
 * must not stop her hands.
 */
export function resolveBusySend(opts: {
  explicit?: BusySendMode
  hasAttachments?: boolean
  steered: boolean
}): 'steered' | 'after' | 'interrupt' {
  const hasAtt = Boolean(opts.hasAttachments)
  const explicit = opts.explicit
  const trySteer =
    !hasAtt && explicit !== 'after' && explicit !== 'interrupt'
  if (trySteer && opts.steered) return 'steered'
  if (explicit === 'interrupt') return 'interrupt'
  return 'after'
}
