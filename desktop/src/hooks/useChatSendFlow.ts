/**
 * Slash commands, send, edit-and-resend, regenerate.
 * Extracted from App.tsx.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
import { getSettings } from '../api/settings'
import { shouldConfirmNewTurn } from '../sessions/concurrentTurns'
import type { ChatSession } from '../types'
import { looksLikeBuildKick } from '../utils/buildKick'
import { isPlaceholderTitle, titleFromPrompt } from '../utils/sessionTitle'

type AttachmentMeta = {
  path: string
  name?: string
  mime?: string
  size?: number
  is_image?: boolean
  is_text?: boolean
}

export function useChatSendFlow(opts: {
  activeId: string | null
  setActiveId: (id: string | null) => void
  sessions: ChatSession[]
  create: (
    title?: string,
    bind?: { provider?: string; model?: string },
  ) => Promise<
    | { id: string; llm_provider?: string | null; model?: string | null }
    | null
    | undefined
  >
  rename: (id: string, title: string) => void | Promise<unknown>
  refreshSessions: () => Promise<unknown>
  send: (
    text: string,
    model?: string,
    sessionId?: string,
    attachments?: AttachmentMeta[],
    planMode?: boolean,
    opts?: { mode?: 'after' | 'interrupt'; provider?: string },
  ) => unknown
  runCommand: (
    command: string,
    sessionId: string,
  ) => Promise<Record<string, unknown>>
  addCommandMessage: (command: string, text: string) => void
  handleExport: (sessionId: string) => Promise<void>
  handleImport: () => Promise<void>
  handleNewSession: () => Promise<void>
  stop: () => void
  clearQueue: () => void
  clearLocalHistory: () => void
  reloadMessages: (opts?: { force?: boolean }) => Promise<unknown>
  beginEdit: (
    msgId: string,
    content?: string,
  ) => Promise<string | null | undefined>
  messages: Array<{ id: string; role: string; content?: string; reverted?: boolean }>
  streaming: boolean
  model: string
  planMode: boolean
  setPlanMode: (v: boolean | ((p: boolean) => boolean)) => void
  notify: (title: string, opts?: { body?: string; silent?: boolean }) => void
  runningCount: number
  sessionLlmMap: Record<string, { provider: string; model: string }>
  llmProvider: string
  barProvider: string
  barModel: string
  setSessionBind: (sessionId: string, provider: string, modelId: string) => void
  setOpenTabs: Dispatch<SetStateAction<Set<string>>>
}) {
  const {
    activeId,
    setActiveId,
    sessions,
    create,
    rename,
    refreshSessions,
    send,
    runCommand,
    addCommandMessage,
    handleExport,
    handleImport,
    handleNewSession,
    stop,
    clearQueue,
    clearLocalHistory,
    reloadMessages,
    beginEdit,
    messages,
    streaming,
    model,
    planMode,
    setPlanMode,
    notify,
    runningCount,
    sessionLlmMap,
    llmProvider,
    barProvider,
    barModel,
    setSessionBind,
    setOpenTabs,
  } = opts

  const notifyArmRef = useRef<string | null>(null)
  const [editDraft, setEditDraft] = useState<{ text: string; key: number } | null>(
    null,
  )
  const [concurrentConfirm, setConcurrentConfirm] = useState<{
    text: string
    attachments?: AttachmentMeta[]
    opts?: { mode?: 'after' | 'interrupt' }
  } | null>(null)
  const skipConcurrentConfirmRef = useRef(false)

  useEffect(() => {
    setEditDraft(null)
  }, [activeId])

  const handleCommand = useCallback(
    async (command: string) => {
      const stripped = command.trim().toLowerCase()
      // Client-side session file export / import (download + file picker).
      if (stripped === '/export' || stripped === '/export-session') {
        const sid = activeId || (await create())?.id
        if (!sid) return { text: 'No session available to export.' }
        await handleExport(sid)
        if (sid) {
          addCommandMessage(command, 'Exported session as plain-text `.txt` (download started).')
        }
        return { text: 'Exported session as .txt', action: 'export_session' }
      }
      if (
        stripped === '/import-session' ||
        stripped === '/session-import' ||
        stripped.startsWith('/import-session ') ||
        stripped.startsWith('/session-import ')
      ) {
        const pathArg = command
          .trim()
          .replace(/^\/(import-session|session-import)\s*/i, '')
          .trim()
        if (pathArg) {
          const sid = activeId || (await create())?.id
          if (!sid) return { text: 'No session available.' }
          const result = await runCommand(command, sid)
          if (result.text) addCommandMessage(command, String(result.text))
          const resultSid =
            typeof result.session_id === 'string' ? result.session_id : ''
          if (resultSid) {
            await refreshSessions()
            setActiveId(resultSid)
            setOpenTabs((prev) => new Set([...prev, resultSid]))
          } else if (result.action === 'import_session_done') {
            await refreshSessions()
          }
          return result
        }
        await handleImport()
        return { text: 'Import session…', action: 'import_session' }
      }

      // /reset needs an existing session — never create one just to wipe it.
      if (stripped === '/reset' || stripped === '/clear') {
        if (!activeId) {
          return {
            text: 'No active session to reset. Open a chat first, or use `/new`.',
          }
        }
        // Quiet the stream first so abort does not race the wipe.
        stop()
        clearQueue()
        const result = await runCommand(command, activeId)
        const ok =
          result.action === 'reset_session'
          || (typeof result.cleared === 'number' && result.cleared >= 0
            && !String(result.text || '').startsWith('Error executing')
            && !String(result.text || '').toLowerCase().includes('could not reset')
            && !String(result.text || '').toLowerCase().includes('unknown command'))

        if (ok) {
          // Instant empty feed — stay on this session id (no jump to /new).
          clearLocalHistory()
          try {
            const { clearChatMediaCache } = await import('../utils/chatMedia')
            clearChatMediaCache()
          } catch {
            /* ignore */
          }
          // Best-effort revalidate; load() swallows errors into loadError.
          await reloadMessages({ force: true })
          // Never leave "Cannot reach local API" up after a confirmed wipe —
          // history is empty either way; confirmation bubble is enough.
          clearLocalHistory()
          try {
            await refreshSessions()
          } catch {
            /* ignore */
          }
          const note =
            (typeof result.text === 'string' && result.text)
            || 'Session fully reset. Same tab — send a message to start as if new.'
          addCommandMessage(command, note)
          return { ...result, text: note, action: result.action || 'reset_session' }
        }

        // Failed — keep history; show the real error.
        if (result.text) addCommandMessage(command, String(result.text))
        return result
      }

      const sid = activeId || (await create())?.id
      if (!sid) return { text: 'No session available.' }
      const result = await runCommand(command, sid)
      if (result.action === 'reset_session') {
        stop()
        clearQueue()
        clearLocalHistory()
        try {
          await reloadMessages({ force: true })
        } catch {
          clearLocalHistory()
        }
        try {
          await refreshSessions()
        } catch {
          /* */
        }
        if (result.text) addCommandMessage(command, String(result.text))
        return result
      }
      if (result.text && sid) {
        addCommandMessage(command, String(result.text))
      }
      if (result.action === 'new_session') {
        await handleNewSession()
      }
      const importSid =
        typeof result.session_id === 'string' ? result.session_id : ''
      if (result.action === 'import_session_done' && importSid) {
        await refreshSessions()
        setActiveId(importSid)
        setOpenTabs((prev) => new Set([...prev, importSid]))
      }
      return result
    },
    [
      runCommand,
      handleNewSession,
      activeId,
      create,
      addCommandMessage,
      handleExport,
      handleImport,
      refreshSessions,
      setActiveId,
      stop,
      clearQueue,
      clearLocalHistory,
      reloadMessages,
    ],
  )

  const handleSend = useCallback(
    async (
      text: string,
      attachments?: {
        path: string
        name?: string
        mime?: string
        size?: number
        is_image?: boolean
        is_text?: boolean
      }[],
      opts?: { mode?: 'after' | 'interrupt' },
    ) => {
      // Clear edit prefill once the user sends (revised prompt is on its way).
      setEditDraft(null)
      if (text.startsWith('/') && !attachments?.length) {
        await handleCommand(text)
      } else {
        let sid = activeId
        if (!sid) {
          const created = await create()
          sid = created?.id ?? null
          if (sid) setOpenTabs((prev) => new Set([...prev, sid!]))
        }
        if (!sid) {
          notify('Chat not ready', {
            body: 'Could not open a session — wait a second for the local server, then try New Session.',
          })
          return
        }
        // Ensure a model id is set before streaming (first paint race after boot).
        // Never pull global settings into the status bar when a session is open —
        // that flipped the second tab (e.g. SecretFolder) between providers.
        let useModel = model
        if (!useModel?.trim()) {
          try {
            const s = await getSettings()
            if (s.llm_model) useModel = s.llm_model
          } catch {
            /* keep empty — server may still default */
          }
        }
        // Optimistic auto-title from first prompt (server also renames placeholders).
        const sess = sessions.find((s) => s.id === sid)
        if (sess && isPlaceholderTitle(sess.title) && (text.trim() || attachments?.length)) {
          const title = titleFromPrompt(
            text.trim() || attachments?.[0]?.name || 'Attachments',
          )
          void rename(sid, title)
        }
        // Session log bug: user said "proceed out of plan mode" / "proceed with all
        // fixes" while Plan was still on → only plan tools → felt stuck. Auto-leave
        // Plan on build/proceed kicks so Build tools actually load.
        let usePlan = planMode
        if (usePlan && looksLikeBuildKick(text)) {
          usePlan = false
          setPlanMode(false)
        }
        // Concurrent turn guard: 3+ live jobs → confirm (other models/sessions).
        const otherRunning = Math.max(
          0,
          runningCount - (streaming ? 1 : 0),
        )
        if (
          !opts?.mode
          && !skipConcurrentConfirmRef.current
          && shouldConfirmNewTurn(otherRunning)
          && !streaming
        ) {
          setConcurrentConfirm({ text, attachments, opts })
          return
        }
        skipConcurrentConfirmRef.current = false
        // Sticky multi-tab bind: map for *this* sid only (never the other tab).
        const mapOv = sessionLlmMap[sid!]
        const useProvider =
          mapOv?.provider
          || sess?.llm_provider
          || barProvider
          || llmProvider
          || undefined
        if (mapOv?.model) useModel = mapOv.model
        else if (sess?.model && sess.llm_provider) useModel = sess.model
        else if (barModel) useModel = barModel
        // Persist bind before stream so list polls cannot re-seed empty.
        if (sid && useProvider && useModel) {
          setSessionBind(sid, String(useProvider), String(useModel))
        }
        // While streaming, send() queues (after) or interrupts based on opts.mode.
        if (sid) notifyArmRef.current = sid
        void send(text, useModel, sid, attachments, usePlan, {
          ...opts,
          provider: useProvider || undefined,
        })
        window.setTimeout(() => {
          void refreshSessions()
        }, 1200)
      }
    },
    [
      send,
      model,
      handleCommand,
      activeId,
      create,
      sessions,
      rename,
      refreshSessions,
      planMode,
      setPlanMode,
      notify,
      runningCount,
      streaming,
      sessionLlmMap,
      llmProvider,
      barProvider,
      barModel,
      setSessionBind,
    ],
  )

  const handleEditUserMessage = useCallback(
    async (msgId: string, content: string) => {
      if (!activeId || streaming) return
      // Immediately put the full original prompt in the chat bar (don't wait on API).
      const localText = content ?? ''
      setEditDraft({ text: localText, key: Date.now() })
      // Soft-delete this message + later ones on the server; refresh history.
      const serverText = await beginEdit(msgId, localText)
      // Prefer server content if it differs (authoritative), re-apply with new key.
      if (serverText != null && serverText !== localText) {
        setEditDraft({ text: serverText, key: Date.now() })
      }
    },
    [activeId, streaming, beginEdit],
  )

  /** Regenerate: roll back to the preceding user turn and resend the same prompt. */
  const handleRegenerate = useCallback(
    async (assistantMsgId: string) => {
      if (!activeId || streaming) return
      const idx = messages.findIndex((m) => m.id === assistantMsgId)
      if (idx < 0) return
      let userIdx = -1
      for (let i = idx - 1; i >= 0; i--) {
        if (messages[i]?.role === 'user' && !(messages[i] as { reverted?: boolean })?.reverted) {
          userIdx = i
          break
        }
      }
      if (userIdx < 0) return
      const userMsg = messages[userIdx]!
      const prompt = userMsg.content || ''
      // Strip attachment display block for resend text if present
      const clean = prompt.replace(/\n\n📎 Attachments:\n[\s\S]*$/, '').trim()
      await beginEdit(userMsg.id, clean)
      const toSend = clean || prompt.trim() || '(see attached files)'
      const sid = activeId
      const bind = sessionLlmMap[sid]
      notifyArmRef.current = sid
      send(toSend, bind?.model || model, sid, undefined, planMode, {
        provider: bind?.provider,
      })
    },
    [activeId, streaming, messages, beginEdit, send, model, sessionLlmMap, planMode],
  )

  // Notify only when a turn we started on *this* session ends — not on tab switch.
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    const was = wasStreamingRef.current
    wasStreamingRef.current = streaming
    if (
      was
      && !streaming
      && notifyArmRef.current
      && notifyArmRef.current === activeId
      && messages.length > 0
    ) {
      notifyArmRef.current = null
      const last = messages[messages.length - 1]
      if (last && last.role === 'assistant' && last.content) {
        notify('Remedy', {
          body: `Response ready — ${last.content.slice(0, 80)}...`,
          silent: false,
        })
      }
    }
  }, [streaming, messages, notify, activeId])


  return {
    editDraft,
    setEditDraft,
    concurrentConfirm,
    setConcurrentConfirm,
    skipConcurrentConfirmRef,
    handleCommand,
    handleSend,
    handleEditUserMessage,
    handleRegenerate,
  }
}
