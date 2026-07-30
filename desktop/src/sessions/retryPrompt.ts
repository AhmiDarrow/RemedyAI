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
}

/** Options passed to send() after Stop & retry. */
export function retrySendOptions(pending: RetryPromptSnapshot): {
  mode: 'after'
  provider?: string
} {
  return {
    mode: 'after',
    // Must forward provider — omitting it rebinds the tab to global LLM settings.
    provider: pending.provider,
  }
}

/** Options when promoting a queued item to interrupt the live turn. */
export function promoteQueuedOptions(item: {
  provider?: string
}): {
  mode: 'interrupt'
  provider?: string
} {
  return {
    mode: 'interrupt',
    provider: item.provider,
  }
}
