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
