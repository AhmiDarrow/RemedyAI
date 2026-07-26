import { useState, useCallback, useEffect, useMemo } from 'react'
import { Sidebar } from './components/Sidebar'
import { ApprovalBanner } from './components/ApprovalBanner'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar, type ThinkingLevel, type ApprovalMode } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { SettingsPanel } from './components/SettingsPanel'
import { TokenCostTicker } from './components/TokenCostTicker'
import { TimeTravelTimeline } from './components/TimeTravelTimeline'
import { estimateCostUsd, type UsageSnapshot } from './utils/tokenCost'
import { HelpPanel } from './components/HelpPanel'
import { QuitServerWarning } from './components/QuitServerWarning'
import { SplashScreen } from './components/SplashScreen'
import { SetupWizard } from './components/SetupWizard'
import { UpdateScreen } from './components/UpdateScreen'
import { TitleBar, type AppMenuAction } from './components/TitleBar'
import { UserNamePrompt } from './components/UserNamePrompt'
import { CommandPalette, type CommandItem } from './components/CommandPalette'
import { useSessions } from './hooks/useSessions'
import { useMessages } from './hooks/useMessages'
import { useTheme } from './hooks/useTheme'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { useNotifications } from './hooks/useNotifications'
import { useUpdateChecker } from './hooks/useUpdateChecker'
import { listAgents, listCommands, exportSession, importSession } from './api/messages'
import { apiFetch } from './api/client'
import { getSettings, updateSettings } from './api/settings'
import {
  listConnectedProviders,
  setSessionLlm as applySessionLlm,
  type ConnectedProvider,
} from './api/providers'
import { UsageDashboard } from './components/UsageDashboard'
import { isPlaceholderTitle, titleFromPrompt } from './utils/sessionTitle'
import { tauriInvoke, tauriListen } from './api/tauri'
import { normalizeToolProcess, type ToolProcessMode } from './utils/toolLabels'
import { looksLikeBuildKick } from './utils/buildKick'
import { HOTKEYS } from './hotkeys'
import type { ShortcutDef } from './hooks/useKeyboardShortcuts'

export interface ModelInfo {
  id: string
  name: string
  provider: string
  default: boolean
}

type ServerState = 'connecting' | 'ready' | 'error'

function isTauri(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as any
  return !!(w.__TAURI__ || w.__TAURI_INTERNALS__ || w.isTauri)
}

/** Window shell: themed custom title bar + content (replaces white OS chrome). */
function AppShell({
  children,
  version,
  updateAvailable,
  onMenuAction,
  helpOpen,
  helpArticleId,
  onCloseHelp,
}: {
  children: React.ReactNode
  version?: string
  updateAvailable?: boolean
  onMenuAction?: (action: AppMenuAction) => void
  helpOpen?: boolean
  helpArticleId?: string | null
  onCloseHelp?: () => void
}) {
  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-primary)' }}>
      <TitleBar
        version={version}
        updateAvailable={updateAvailable}
        onMenuAction={onMenuAction}
      />
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
      {onCloseHelp && (
        <HelpPanel
          open={Boolean(helpOpen)}
          onClose={onCloseHelp}
          initialArticleId={helpArticleId}
          version={version}
        />
      )}
    </div>
  )
}

export default function App() {
  const {
    sessions,
    activeId,
    setActiveId,
    create,
    createInProject,
    setProject: setSessionProject,
    bulkSetProject,
    hasMore: sessionsHasMore,
    loadingMore: sessionsLoadingMore,
    loadMore: loadMoreSessions,
    remove,
    rename,
    refresh: refreshSessions,
  } = useSessions()
  const {
    messages,
    loading: messagesLoading,
    streaming,
    partialText,
    partialThinking,
    activeTools,
    processSteps,
    taskProgress,
    runUsage,
    send,
    stop,
    runCommand,
    addCommandMessage,
    beginEdit,
    load: reloadMessages,
  } = useMessages(activeId)
  /** Prefill for edit-and-resend; `key` forces re-apply even for identical text. */
  const [editDraft, setEditDraft] = useState<{ text: string; key: number } | null>(null)
  // Don't carry an edit draft across session switches.
  useEffect(() => {
    setEditDraft(null)
  }, [activeId])

  const {
    themeId,
    theme,
    set: setTheme,
    density,
    setDensity,
    customAccent,
    setCustomAccent,
  } = useTheme()
  const [model, setModel] = useState('gpt-4o-mini')
  const [llmProvider, setLlmProvider] = useState('openai')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('high')
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('ask')
  const [toolProcessMode, setToolProcessMode] = useState<ToolProcessMode>('off')
  const [planMode, setPlanMode] = useState(false)
  const [panel, setPanel] = useState<'memory' | 'skills' | 'settings' | null>(null)
  const [openTabs, setOpenTabs] = useState<Set<string>>(new Set())
  const [serverState, setServerState] = useState<ServerState>(isTauri() ? 'connecting' : 'ready')
  const [serverError, setServerError] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [agentDefs, setAgentDefs] = useState<{ name: string; description: string }[]>([])
  const { notify } = useNotifications()
  const {
    updateInfo,
    desktopInfo,
    checking: checkingUpdates,
    check: checkUpdates,
    lastStatus: updateLastStatus,
    updateAvailable,
  } = useUpdateChecker()
  const [showSetupWizard, setShowSetupWizard] = useState(false)
  const [showUpdateScreen, setShowUpdateScreen] = useState(false)
  const [userName, setUserName] = useState('')
  const [askUserName, setAskUserName] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [appVersion, setAppVersion] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [helpArticleId, setHelpArticleId] = useState<string | null>(null)
  const [quitWarnOpen, setQuitWarnOpen] = useState(false)
  const [timeTravelOpen, setTimeTravelOpen] = useState(false)
  const [usageOpen, setUsageOpen] = useState(false)
  const [connectedProviders, setConnectedProviders] = useState<ConnectedProvider[]>([])
  /** Per-session provider/model overrides (tabs stay independent). */
  const [sessionLlmMap, setSessionLlmMap] = useState<
    Record<string, { provider: string; model: string }>
  >({})
  const [switchToast, setSwitchToast] = useState<string | null>(null)

  // Hydrate session LLM map from server session records
  useEffect(() => {
    setSessionLlmMap((prev) => {
      const next = { ...prev }
      for (const s of sessions) {
        if (s.llm_provider && s.model) {
          next[s.id] = { provider: s.llm_provider, model: s.model }
        } else if (s.model && !next[s.id]) {
          // model-only legacy sessions keep map empty until first switch
        }
      }
      return next
    })
  }, [sessions])

  // Restore per-session provider/model when switching tabs
  useEffect(() => {
    if (!activeId) return
    const ov = sessionLlmMap[activeId]
    if (ov) {
      setLlmProvider(ov.provider)
      setModel(ov.model)
      return
    }
    const sess = sessions.find((s) => s.id === activeId)
    if (sess?.llm_provider) {
      setLlmProvider(sess.llm_provider)
      if (sess.model) setModel(sess.model)
    }
  }, [activeId, sessionLlmMap, sessions])

  const sessionUsage: UsageSnapshot = useMemo(() => {
    let prompt = 0
    let completion = 0
    for (const m of messages) {
      if (m.reverted) continue
      if (m.role === 'user') {
        prompt += Math.ceil((m.content || '').length / 4)
      } else if (m.role === 'assistant') {
        if (typeof m.tokens === 'number' && m.tokens > 0) {
          completion += m.tokens
        } else {
          completion += Math.ceil(
            ((m.content || '') + (m.thinking || '')).length / 4,
          )
        }
      }
    }
    // Prefer live run totals when they include provider prompt counts
    if (runUsage && runUsage.total_tokens > 0 && !streaming) {
      /* keep session sum of stored messages */
    }
    return {
      prompt_tokens: prompt,
      completion_tokens: completion,
      total_tokens: prompt + completion,
      estimated_cost_usd: estimateCostUsd(prompt, completion, model, llmProvider),
      source: 'estimate',
      model,
      provider: llmProvider,
    }
  }, [messages, model, llmProvider, runUsage, streaming])

  const openHelp = useCallback((articleId?: string) => {
    setHelpArticleId(articleId || null)
    setHelpOpen(true)
  }, [])

  const confirmQuitApp = useCallback(async (dontWarnAgain: boolean) => {
    setQuitWarnOpen(false)
    if (!isTauri()) {
      window.close()
      return
    }
    try {
      const { tauriInvoke } = await import('./api/tauri')
      if (dontWarnAgain) {
        try {
          const prefs = await tauriInvoke<{
            close_to_tray?: boolean
            start_in_tray?: boolean
          }>('get_desktop_prefs')
          await tauriInvoke('set_desktop_prefs', {
            close_to_tray: Boolean(prefs?.close_to_tray ?? true),
            start_in_tray: Boolean(prefs?.start_in_tray ?? false),
            skip_quit_server_warning: true,
          })
        } catch (e) {
          console.warn('save skip_quit_server_warning:', e)
          try {
            localStorage.setItem('remedy.skipQuitServerWarning', '1')
          } catch {
            /* */
          }
        }
      }
      await tauriInvoke('quit_app')
    } catch (e) {
      console.warn('quit_app failed:', e)
    }
  }, [])

  const requestQuitWithWarning = useCallback(async () => {
    if (!isTauri()) {
      setQuitWarnOpen(true)
      return
    }
    try {
      // localStorage fast-path
      try {
        if (localStorage.getItem('remedy.skipQuitServerWarning') === '1') {
          const { tauriInvoke } = await import('./api/tauri')
          await tauriInvoke('quit_app')
          return
        }
      } catch {
        /* */
      }
      const { tauriInvoke } = await import('./api/tauri')
      const res = await tauriInvoke<{ needs_confirm?: boolean; quitting?: boolean }>(
        'request_quit_app',
      )
      if (res?.needs_confirm) {
        setQuitWarnOpen(true)
      }
      // if already quitting, no dialog
    } catch {
      setQuitWarnOpen(true)
    }
  }, [])

  // Dev / browser review: open wiki with ?help=1 or ?help=09-troubleshooting
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      const h = params.get('help')
      if (h === null) return
      openHelp(h === '1' || h === '' || h === 'true' ? undefined : h)
    } catch {
      /* ignore */
    }
  }, [openHelp])

  /** Run update check with visible UI (settings About section), not a silent focus. */
  const runUpdateCheckVisible = useCallback(async () => {
    setPanel('settings')
    const result = await checkUpdates()
    if (result.updateAvailable && result.desktopInfo?.download_url) {
      setShowUpdateScreen(true)
    }
    return result
  }, [checkUpdates])

  const handleMenuAction = useCallback(
    (action: AppMenuAction) => {
      switch (action) {
        case 'settings':
          setPanel('settings')
          break
        case 'memory':
          setPanel('memory')
          break
        case 'skills':
          setPanel('skills')
          break
        case 'help':
          openHelp()
          break
        case 'switch_web_ui':
          void (async () => {
            if (!isTauri()) {
              // Browser already — open API web UI in a new tab
              window.open('http://127.0.0.1:7400/', '_blank', 'noopener,noreferrer')
              return
            }
            try {
              const { tauriInvoke } = await import('./api/tauri')
              await tauriInvoke<string>('switch_to_web_ui')
            } catch (e: unknown) {
              const msg = e instanceof Error ? e.message : String(e)
              console.warn('switch_to_web_ui failed:', msg)
              // Fallback: hide-to-tray + open URL ourselves
              try {
                const { tauriInvoke } = await import('./api/tauri')
                await tauriInvoke('request_close_main_window')
              } catch {
                /* ignore */
              }
              try {
                const { openExternalUrl } = await import('./api/auth')
                await openExternalUrl('http://127.0.0.1:7400/')
              } catch {
                window.open('http://127.0.0.1:7400/', '_blank', 'noopener,noreferrer')
              }
            }
          })()
          break
        case 'new_session':
          void (async () => {
            const s = await create()
            if (s?.id) setOpenTabs((prev) => new Set([...prev, s.id]))
          })()
          break
        case 'check_updates':
          void runUpdateCheckVisible()
          break
        case 'install_update':
          if (desktopInfo?.update_available && desktopInfo.download_url) {
            setShowUpdateScreen(true)
          } else {
            void runUpdateCheckVisible()
          }
          break
        case 'about':
          setAboutOpen(true)
          break
        case 'quit':
          void requestQuitWithWarning()
          break
        default:
          break
      }
    },
    [runUpdateCheckVisible, desktopInfo, create, openHelp, requestQuitWithWarning],
  )

  // Tray Quit / window close when not hide-to-tray → show server-stop warning
  useEffect(() => {
    if (!isTauri()) return
    let off: (() => void) | undefined
    void tauriListen('app-quit-requested', () => {
      setQuitWarnOpen(true)
    }).then((fn) => {
      off = fn
    })
    return () => {
      off?.()
    }
  }, [])

  useEffect(() => {
    if (!isTauri()) return
    let off: Array<() => void> = []
    void (async () => {
      off.push(
        await tauriListen('server-ready', () => {
          setServerState('ready')
        }),
      )
      off.push(
        await tauriListen('server-error', (payload) => {
          setServerState('error')
          setServerError(typeof payload === 'string' ? payload : 'Server failed to start')
        }),
      )
    })()
    return () => {
      for (const u of off) u()
    }
  }, [])

  // Tray menu → themed in-app panels (must open Settings, not just focus chat)
  useEffect(() => {
    if (!isTauri()) return
    let off: Array<() => void> = []
    void (async () => {
      off.push(await tauriListen('tray-open-settings', () => setPanel('settings')))
      off.push(
        await tauriListen('tray-check-updates', () => {
          void runUpdateCheckVisible()
        }),
      )
      off.push(await tauriListen('tray-about', () => setAboutOpen(true)))
    })()
    return () => {
      for (const u of off) u()
    }
  }, [runUpdateCheckVisible])

  /** Refresh model list only — does not change the selected model unless asked. */
  const refreshModels = useCallback(async (opts?: { selectDefault?: boolean }) => {
    try {
      const data = await apiFetch<{ models: ModelInfo[]; default: string; provider?: string }>('/models')
      setModels(data.models)
      if (opts?.selectDefault) {
        const def = data.models.find((m) => m.id === data.default) ?? data.models[0]
        if (def) setModel(def.id)
      }
      return data
    } catch (e: unknown) {
      console.warn('Model refresh failed:', e instanceof Error ? e.message : e)
      return null
    }
  }, [])

  useEffect(() => {
    if (serverState !== 'ready') return
    let cancelled = false
    ;(async () => {
      // Prefer the token splash already warmed — do not force-clear (extra IPC/HTTP).
      try {
        const { ensureApiToken } = await import('./api/client')
        await ensureApiToken()
      } catch {
        /* continue — settings may still work offline later */
      }
      if (cancelled) return

      // Settings first: first-run wizard must not depend on models/agents succeeding.
      // Sessions load in parallel — sidebar should not wait on models.
      let settings: Awaited<ReturnType<typeof getSettings>> | null = null
      const sessionsPromise = refreshSessions()
      try {
        settings = await getSettings()
      } catch (e: unknown) {
        console.warn('getSettings failed, retrying auth:', e)
        try {
          const { clearApiToken, ensureApiToken } = await import('./api/client')
          clearApiToken()
          await ensureApiToken()
          settings = await getSettings()
        } catch (e2: unknown) {
          console.warn('getSettings retry failed:', e2)
        }
      }
      if (cancelled) return
      void sessionsPromise

      if (!settings) {
        // Fresh / wiped installs: still open setup so the user is not stuck on
        // "Failed to load server config" when the API is only partially up.
        // Open setup + Retry remain available if save still fails.
        setShowSetupWizard(true)
        setServerError(
          'Could not load settings yet — complete setup once the local server is ready. '
          + 'If save fails, use Retry then Open setup.',
        )
        return
      }

      // True first run: no completed setup → wizard before chat UI
      const needsWizard =
        Boolean(settings.needs_setup)
        || settings.setup_completed === false
        || settings.config_exists === false

      if (needsWizard) {
        setShowSetupWizard(true)
      }

      if (settings) {
        if (settings.llm_model) setModel(settings.llm_model)
        if (settings.llm_provider) setLlmProvider(settings.llm_provider)
        try {
          const conn = await listConnectedProviders()
          setConnectedProviders(conn.picker?.length ? conn.picker : conn.connected || [])
          if (conn.active_provider) setLlmProvider(conn.active_provider)
          if (conn.active_model) setModel(conn.active_model)
        } catch {
          /* picker falls back to models-only */
        }
        const tl = String(settings.thinking_level || 'high').toLowerCase()
        if (tl === 'off' || tl === 'low' || tl === 'medium' || tl === 'high') {
          setThinkingLevel(tl)
        }
        const am = String(settings.approval_mode || 'ask').toLowerCase()
        if (am === 'ask' || am === 'auto') setApprovalMode(am)
        setToolProcessMode(normalizeToolProcess(settings.tool_process ?? settings.show_tool_calls))
        const un = (settings.user_name || '').trim()
        setUserName(un)
        if (settings.version) setAppVersion(String(settings.version))
        if (!needsWizard && !un) {
          try {
            const skipped = localStorage.getItem('remedy.userName.skipped')
            if (!skipped) setAskUserName(true)
          } catch {
            setAskUserName(true)
          }
        }
      }

      // Secondary loads — never kill first-run / chat shell if these fail
      try {
        const [modelsData, agents] = await Promise.all([
          refreshModels({ selectDefault: !settings?.llm_model }),
          listAgents().catch(() => null),
        ])
        if (cancelled) return
        if (agents) {
          setAgentDefs(Array.isArray(agents) ? agents : (agents as { agents?: typeof agentDefs }).agents || [])
        }
        if (!settings?.llm_model && modelsData?.default) {
          setModel(modelsData.default)
        }
        void listCommands().catch(() => null)
      } catch (e: unknown) {
        // Settings already loaded (or wizard already open). Models/agents failures
        // must not block first-run or the chat shell.
        console.warn('Secondary startup load failed:', e)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [serverState, refreshModels, refreshSessions])

  const handleNewSession = useCallback(async () => {
    const s = await create()
    if (s) {
      setOpenTabs((prev) => new Set([...prev, s.id]))
    }
  }, [create])

  const handleSelect = useCallback(
    (id: string) => {
      setActiveId(id)
      setOpenTabs((prev) => {
        if (prev.has(id)) return prev
        return new Set([...prev, id])
      })
    },
    [setActiveId],
  )

  const handleCloseTab = useCallback(
    (id: string) => {
      setOpenTabs((prev) => {
        const next = new Set(prev)
        next.delete(id)
        if (activeId === id && next.size > 0) {
          setActiveId([...next][0])
        } else if (next.size === 0) {
          setActiveId(null)
        }
        return next
      })
    },
    [activeId, setActiveId],
  )

  const handleExport = useCallback(
    async (sessionId: string) => {
      try {
        const { text, markdown, filename } = await exportSession(sessionId, 'txt')
        const body = text || markdown || ''
        if (!body.trim()) {
          notify('Export failed', { body: 'Session export was empty' })
          return
        }
        const safeName = (
          filename.endsWith('.txt') || filename.endsWith('.md')
            ? filename
            : `${filename || 'remedy-export'}.txt`
        ).replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')

        // Tauri: native Save dialog (WebView <a download> is unreliable).
        if (isTauri()) {
          try {
            const saved = await tauriInvoke<string | null>('save_text_file', {
              defaultName: safeName,
              contents: body,
            })
            if (saved) {
              notify('Exported session', { body: saved, silent: true })
              return
            }
            // User cancelled dialog — not an error
            notify('Export cancelled', { silent: true })
            return
          } catch (nativeErr) {
            console.warn('Native save failed, trying browser download:', nativeErr)
          }
        }

        const blob = new Blob([body], { type: 'text/plain;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = safeName
        a.rel = 'noopener'
        a.style.display = 'none'
        document.body.appendChild(a)
        a.click()
        window.setTimeout(() => {
          a.remove()
          URL.revokeObjectURL(url)
        }, 1500)
        notify('Exported session', { body: safeName, silent: true })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        console.warn('Export failed:', msg)
        notify('Export failed', { body: msg })
      }
    },
    [notify],
  )

  const handleImport = useCallback(async () => {
    try {
      const input = document.createElement('input')
      input.type = 'file'
      input.accept = '.txt,.md,text/plain,text/markdown'
      input.style.display = 'none'
      const file = await new Promise<File | null>((resolve) => {
        input.onchange = () => resolve(input.files?.[0] ?? null)
        input.oncancel = () => resolve(null)
        document.body.appendChild(input)
        input.click()
        window.setTimeout(() => {
          if (input.parentNode) input.parentNode.removeChild(input)
        }, 60_000)
      })
      if (!file) return
      const text = await file.text()
      if (!text.trim()) {
        notify('Import failed', { body: 'File is empty' })
        return
      }
      const stem = file.name.replace(/\.(txt|md)$/i, '').trim()
      const title =
        stem && !stem.toLowerCase().startsWith('remedy-export') ? stem : undefined
      const created = await importSession({ text, title })
      await refreshSessions()
      if (created?.id) {
        setActiveId(created.id)
        setOpenTabs((prev) => new Set([...prev, created.id]))
        notify('Session imported', {
          body: created.title || `${created.imported_messages ?? ''} messages`,
          silent: true,
        })
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      console.warn('Import failed:', msg)
      notify('Import failed', { body: msg })
    }
  }, [refreshSessions, setActiveId, notify])

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
          if (result.text) addCommandMessage(command, result.text)
          if (result.session_id) {
            await refreshSessions()
            setActiveId(result.session_id)
            setOpenTabs((prev) => new Set([...prev, result.session_id as string]))
          } else if (result.action === 'import_session_done') {
            await refreshSessions()
          }
          return result
        }
        await handleImport()
        return { text: 'Import session…', action: 'import_session' }
      }

      const sid = activeId || (await create())?.id
      if (!sid) return { text: 'No session available.' }
      const result = await runCommand(command, sid)
      if (result.text && sid) {
        addCommandMessage(command, result.text)
      }
      if (result.action === 'new_session') {
        await handleNewSession()
      }
      if (result.action === 'import_session_done' && result.session_id) {
        await refreshSessions()
        setActiveId(result.session_id)
        setOpenTabs((prev) => new Set([...prev, result.session_id as string]))
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
    ) => {
      // Clear edit prefill once the user sends (revised prompt is on its way).
      setEditDraft(null)
      if (text.startsWith('/') && !attachments?.length) {
        await handleCommand(text)
      } else {
        const sid = activeId || (await create())?.id
        if (!sid) return
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
        send(text, model, sid, attachments, usePlan)
        // Pull titles/message counts after the turn starts (server may have renamed).
        window.setTimeout(() => {
          void refreshSessions()
        }, 1200)
      }
    },
    [send, model, handleCommand, activeId, create, sessions, rename, refreshSessions, planMode],
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
        if (messages[i]?.role === 'user' && !messages[i]?.reverted) {
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
      if (clean) {
        const sid = activeId
        send(clean, model, sid)
      }
    },
    [activeId, streaming, messages, beginEdit, send, model],
  )

  useEffect(() => {
    if (!streaming && messages.length > 0) {
      const last = messages[messages.length - 1]
      if (last && last.role === 'assistant' && last.content) {
        notify('Remedy', { body: `Response ready — ${last.content.slice(0, 80)}...`, silent: false })
      }
    }
  }, [streaming, messages, notify])

  const paletteCommands: CommandItem[] = useMemo(() => {
    const items: CommandItem[] = [
      { id: 'new', label: 'New Session', description: 'Start a new chat session', category: 'session', action: handleNewSession },
      {
        id: 'time-travel',
        label: 'Time Travel',
        description: 'Timeline: restore chat & files to an earlier step',
        category: 'session',
        action: () => setTimeTravelOpen(true),
      },
      {
        id: 'export',
        label: 'Export Session',
        description: 'Download active session as .txt',
        category: 'session',
        action: () => {
          if (activeId) void handleExport(activeId)
        },
      },
      {
        id: 'import',
        label: 'Import Session',
        description: 'Import a session from .txt / .md',
        category: 'session',
        action: () => void handleImport(),
      },
      { id: 'palette', label: 'Command Palette', description: 'Open this palette', category: 'general', action: () => setPaletteOpen(true) },
      { id: 'plan', label: 'Toggle Plan Mode', description: 'Switch between plan and build', category: 'general', action: () => setPlanMode((p) => !p) },
      { id: 'memory', label: 'Memory Panel', description: 'Toggle memory panel', category: 'panel', action: () => setPanel((p) => (p === 'memory' ? null : 'memory')) },
      { id: 'skills', label: 'Skills Panel', description: 'Toggle skills panel', category: 'panel', action: () => setPanel((p) => (p === 'skills' ? null : 'skills')) },
      { id: 'settings', label: 'Settings Panel', description: 'Toggle settings panel', category: 'panel', action: () => setPanel((p) => (p === 'settings' ? null : 'settings')) },
      {
        id: 'help',
        label: "Help / Owner's Manual",
        description: 'Searchable offline wiki (F1 / Ctrl+/)',
        category: 'general',
        action: () => openHelp(),
      },
      {
        id: 'help-troubleshoot',
        label: 'Troubleshooting',
        description: 'Open Help → Troubleshooting',
        category: 'general',
        action: () => openHelp('09-troubleshooting'),
      },
      {
        id: 'help-commands',
        label: 'Slash commands reference',
        description: 'Open Help → Commands',
        category: 'general',
        action: () => openHelp('11-reference-commands'),
      },
      ...sessions.map((s) => ({
        id: `session-${s.id}`,
        label: s.title || 'Untitled',
        description: `${s.message_count} messages`,
        category: 'session',
        action: () => handleSelect(s.id),
      })),
      ...agentDefs.map((a) => ({
        id: `agent-${a.name}`,
        label: `@${a.name}`,
        description: a.description || '',
        category: 'agent',
        action: () => {},
      })),
      ...models.map((m) => ({
        id: `model-${m.id}`,
        label: m.name,
        description: m.provider,
        category: 'model',
        action: () => setModel(m.id),
      })),
    ]
    return items
  }, [
    sessions,
    agentDefs,
    models,
    handleNewSession,
    handleSelect,
    handleCommand,
    handleExport,
    handleImport,
    activeId,
    openHelp,
  ])

  // Wire global shortcuts from hotkeys.ts (single source of truth for labels + keys).
  const globalShortcuts: ShortcutDef[] = useMemo(() => {
    const byAction: Record<string, () => void> = {
      'New chat session': () => {
        void handleNewSession()
      },
      'Open command palette': () => setPaletteOpen((o) => !o),
      'Toggle plan mode': () => setPlanMode((p) => !p),
      'Open settings': () => setPanel((p) => (p === 'settings' ? null : 'settings')),
      "Open Help wiki (owner's manual)": () => openHelp(),
      'Close panels and command palette': () => {
        // HelpPanel also handles Esc while open; this covers palette / side panels.
        if (helpOpen) {
          setHelpOpen(false)
          return
        }
        setPaletteOpen(false)
        setPanel(null)
      },
    }
    const out: ShortcutDef[] = []
    for (const h of HOTKEYS) {
      if (!h.match || h.scope !== 'global') continue
      const handler = byAction[h.action]
      if (!handler) continue
      const allowInInput =
        h.match.key === 'F1'
        || h.match.key === 'Escape'
        || (h.match.key === '/' && h.match.ctrl)
      out.push({
        key: h.match.key,
        ctrl: h.match.ctrl ?? false,
        shift: h.match.shift ?? false,
        alt: h.match.alt ?? false,
        allowInInput,
        handler,
      })
    }
    return out
  }, [handleNewSession, openHelp, helpOpen])

  useKeyboardShortcuts(globalShortcuts)

  const shellProps = {
    version: appVersion || updateInfo?.current_version || desktopInfo?.current_version,
    updateAvailable,
    onMenuAction: handleMenuAction,
    helpOpen,
    helpArticleId,
    onCloseHelp: () => setHelpOpen(false),
  }

  if (serverState === 'connecting') {
    return (
      <AppShell {...shellProps}>
        <SplashScreen
          onReady={() => {
            // Mark document ready so light theme CSS (if any) can apply after splash.
            try {
              document.documentElement.classList.add('app-ready')
            } catch {
              // ignore
            }
            setServerState('ready')
          }}
          onError={(msg) => {
            setServerState('error')
            setServerError(msg)
          }}
        />
      </AppShell>
    )
  }

  // First-run setup takes priority over hard error when possible
  if (showSetupWizard) {
    return (
      <AppShell {...shellProps}>
        <SetupWizard
          open={showSetupWizard}
          onComplete={() => {
            setShowSetupWizard(false)
            void getSettings()
              .then((s) => {
                if (s.llm_model) setModel(s.llm_model)
                if (s.llm_provider) setLlmProvider(s.llm_provider)
                const un = (s.user_name || '').trim()
                setUserName(un)
                if (!un) setAskUserName(true)
                return refreshModels()
              })
              .catch(() => refreshModels())
          }}
        />
      </AppShell>
    )
  }

  if (serverState === 'error') {
    return (
      <AppShell {...shellProps}>
        <div
          className="flex items-center justify-center h-full flex-col gap-4 px-6"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
        >
          <div style={{ color: 'var(--error)' }} className="text-lg font-medium text-center">
            {serverError || 'Server connection failed'}
          </div>
          <div className="text-sm text-center max-w-md" style={{ color: 'var(--text-muted)' }}>
            The Remedy server could not start or respond. On a fresh install, try
            Retry (starts the local API), then complete setup.
          </div>
          <div className="flex flex-wrap gap-3 justify-center">
            <button
              type="button"
              onClick={() => {
                setServerError('')
                setServerState('connecting')
                if (isTauri()) {
                  void tauriInvoke('restart_server').catch((e: unknown) => {
                    const msg = e instanceof Error ? e.message : String(e)
                    setServerState('error')
                    setServerError(msg || 'Failed to restart server')
                  })
                }
              }}
              className="px-5 py-2 rounded-md text-sm"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              Retry
            </button>
            <button
              type="button"
              onClick={() => {
                // Always offer setup on error (first run / wipe). Warm token first.
                void (async () => {
                  try {
                    const { clearApiToken, ensureApiToken } = await import('./api/client')
                    clearApiToken()
                    await ensureApiToken()
                  } catch {
                    /* wizard will surface save/oauth errors */
                  }
                  setServerError('')
                  setServerState('ready')
                  setShowSetupWizard(true)
                })()
              }}
              className="px-5 py-2 rounded-md text-sm"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
            >
              Open setup
            </button>
            <button
              type="button"
              onClick={() => {
                if (!isTauri()) return
                void tauriInvoke('open_data_folder').catch((e: unknown) => {
                  const msg = e instanceof Error ? e.message : String(e)
                  setServerError((prev) =>
                    `${prev ? prev + ' - ' : ''}Could not open data folder: ${msg || 'Tauri bridge unavailable'}`,
                  )
                })
              }}
              className="px-5 py-2 rounded-md text-sm"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
            >
              Open data folder
            </button>
            <button
              type="button"
              onClick={() => openHelp('09-troubleshooting')}
              className="px-5 py-2 rounded-md text-sm"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
            >
              Troubleshooting help
            </button>
          </div>
        </div>
      </AppShell>
    )
  }

  if (showUpdateScreen && desktopInfo?.update_available && desktopInfo.download_url) {
    return (
      <AppShell {...shellProps}>
        <UpdateScreen
          info={desktopInfo}
          autoStart
          onClose={() => setShowUpdateScreen(false)}
        />
      </AppShell>
    )
  }

  return (
    <AppShell {...shellProps}>
    <div className="flex flex-1 min-h-0" style={{ background: 'var(--bg-primary)' }}>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={paletteCommands}
      />

      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNewSession}
        onNewInProject={(projectPath, opts) => {
          void (async () => {
            const s = await createInProject(projectPath, undefined, opts)
            if (s?.id) {
              setOpenTabs((prev) => new Set([...prev, s.id]))
            }
          })()
        }}
        onSetSessionProject={(id, projectPath) => {
          void setSessionProject(id, projectPath)
        }}
        onBulkSetProject={(ids, projectPath) => {
          void bulkSetProject(ids, projectPath)
        }}
        onBrowseProject={async () => {
          try {
            const path = await tauriInvoke<string | null>('pick_folder')
            return path && path.trim() ? path.trim() : null
          } catch {
            return null
          }
        }}
        hasMore={sessionsHasMore}
        loadingMore={sessionsLoadingMore}
        onLoadMore={() => void loadMoreSessions()}
        onDelete={(id) => {
          remove(id)
          handleCloseTab(id)
        }}
        onRename={(id, title) => {
          void rename(id, title)
        }}
        onExport={handleExport}
        onImport={() => void handleImport()}
        footer={
          <TokenCostTicker
            placement="sidebar"
            run={runUsage}
            session={sessionUsage}
            streaming={streaming}
            model={model}
            provider={llmProvider}
          />
        }
      />

      <div className="flex-1 flex flex-col min-w-0 relative min-h-0">
        <TabBar
          tabs={sessions.filter((s) => openTabs.has(s.id))}
          activeId={activeId}
          onSelect={handleSelect}
          onClose={handleCloseTab}
          onNew={handleNewSession}
          onExport={handleExport}
        />

        {planMode && (
          <div
            className="absolute top-9 right-2 z-10 px-2 py-0.5 text-xs font-semibold rounded pointer-events-none"
            style={{ background: 'var(--accent)', color: '#fff', opacity: 0.9 }}
          >
            Plan Mode
          </div>
        )}

        <div className="flex-1 flex min-h-0">
          {/* Full chat column is the drop target (not only the small composer bar). */}
          <div className="flex-1 flex flex-col min-w-0 min-h-0">
            <ApprovalBanner sessionId={activeId} />
            <MessageFeed
              messages={messages}
              partialText={partialText}
              partialThinking={partialThinking}
              streaming={streaming}
              loading={messagesLoading}
              planMode={planMode}
              activeTools={activeTools}
              processSteps={processSteps}
              taskProgress={taskProgress}
              toolProcessMode={toolProcessMode}
              onEditUserMessage={handleEditUserMessage}
              onQuickPrompt={(text) => void handleSend(text)}
              onRegenerate={(id) => void handleRegenerate(id)}
              userName={userName}
            />

            <Composer
              onSend={handleSend}
              onStop={stop}
              onCommand={handleCommand}
              streaming={streaming}
              // Never lock the prompt while the model streams/thinks — user must
              // always be able to type (and queue the next send after Stop).
              disabled={serverState !== 'ready'}
              planMode={planMode}
              onTogglePlanMode={() => setPlanMode((p) => !p)}
              agents={agentDefs}
              editDraft={editDraft}
              sessionId={activeId}
              llmProvider={llmProvider}
              llmModel={model}
              onOpenSettings={() => setPanel('settings')}
              ensureSession={async () => {
                if (activeId) return activeId
                const s = await create()
                if (s?.id) {
                  setActiveId(s.id)
                  setOpenTabs((prev) => new Set([...prev, s.id]))
                }
                return s?.id ?? null
              }}
            />
          </div>

          <TimeTravelTimeline
            open={timeTravelOpen}
            onClose={() => setTimeTravelOpen(false)}
            sessionId={activeId}
            onRestored={() => {
              void reloadMessages()
              void refreshSessions()
            }}
          />
          <MemoryPanel
            open={panel === 'memory'}
            onClose={() => setPanel(null)}
            sessionId={activeId}
          />
          <SkillsPanel
            open={panel === 'skills'}
            onClose={() => setPanel(null)}
            onOpenHelp={openHelp}
          />
          <SettingsPanel
            open={panel === 'settings'}
            onClose={() => setPanel(null)}
            themeId={themeId}
            onThemeChange={setTheme}
            density={density}
            onDensityChange={setDensity}
            customAccent={customAccent}
            onCustomAccentChange={setCustomAccent}
            updateInfo={updateInfo}
            checkingUpdates={checkingUpdates}
            updateStatus={updateLastStatus}
            onCheckUpdates={() => {
              void runUpdateCheckVisible()
            }}
            onInstallUpdate={() => {
              if (desktopInfo?.update_available && desktopInfo.download_url) {
                setShowUpdateScreen(true)
              } else {
                void runUpdateCheckVisible()
              }
            }}
            models={models}
            toolProcessMode={toolProcessMode}
            onToolProcessChange={(mode) => {
              setToolProcessMode(mode)
              updateSettings({ tool_process: mode }).catch(() => {})
            }}
            onOpenHelp={openHelp}
            onSettingsSaved={() => {
              void getSettings()
                .then((s) => {
                  if (s.llm_model) setModel(s.llm_model)
                  if (s.llm_provider) setLlmProvider(s.llm_provider)
                  setUserName((s.user_name || '').trim())
                  setToolProcessMode(normalizeToolProcess(s.tool_process))
                  return refreshModels()
                })
                .catch(() => refreshModels())
            }}
          />
        </div>

        <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={model}
          models={models}
          provider={llmProvider}
          connectedProviders={connectedProviders}
          onProviderModelChange={(prov, mid) => {
            if (streaming) return
            setLlmProvider(prov)
            setModel(mid)
            if (activeId) {
              setSessionLlmMap((prev) => ({
                ...prev,
                [activeId]: { provider: prov, model: mid },
              }))
            }
            const apply = async () => {
              if (activeId) {
                try {
                  // Session override by default — does not rewrite global default
                  const r = await applySessionLlm(activeId, prov, mid, false)
                  if (r.provider) setLlmProvider(r.provider)
                  if (r.model) setModel(r.model)
                  if (r.toast) {
                    setSwitchToast(r.toast)
                    window.setTimeout(() => setSwitchToast(null), 4200)
                  }
                  return
                } catch {
                  /* fall through to settings */
                }
              }
              await updateSettings({ llm_provider: prov, llm_model: mid })
            }
            void apply()
              .then(() =>
                Promise.all([
                  listConnectedProviders(),
                  apiFetch<{ models: ModelInfo[]; default: string; provider?: string }>(
                    '/models',
                  ).catch(() => null),
                ]),
              )
              .then(([conn, modelsData]) => {
                setConnectedProviders(
                  conn.picker?.length ? conn.picker : conn.connected || [],
                )
                if (modelsData?.models?.length) setModels(modelsData.models)
              })
              .catch(() => {})
          }}
          onModelChange={(id) => {
            setModel(id)
            if (activeId) {
              setSessionLlmMap((prev) => ({
                ...prev,
                [activeId]: {
                  provider: prev[activeId]?.provider || llmProvider,
                  model: id,
                },
              }))
              void applySessionLlm(activeId, llmProvider, id, false)
                .then((r) => {
                  if (r.toast) {
                    setSwitchToast(r.toast)
                    window.setTimeout(() => setSwitchToast(null), 4200)
                  }
                })
                .catch(() => {
                  updateSettings({ llm_model: id }).catch(() => {})
                })
              return
            }
            updateSettings({ llm_model: id })
              .then((r) => {
                if (r.llm_model) setModel(r.llm_model)
              })
              .catch(() => {})
          }}
          onOpenUsage={() => setUsageOpen(true)}
          thinkingLevel={thinkingLevel}
          onThinkingLevelChange={(level) => {
            setThinkingLevel(level)
            updateSettings({ thinking_level: level }).catch(() => {})
          }}
          approvalMode={approvalMode}
          onApprovalModeChange={(mode) => {
            setApprovalMode(mode)
            updateSettings({ approval_mode: mode }).catch(() => {})
          }}
          toolProcessMode={toolProcessMode}
          onToolProcessChange={(mode) => {
            setToolProcessMode(mode)
            updateSettings({ tool_process: mode }).catch(() => {})
          }}
          themeId={themeId}
          theme={theme}
          onThemeChange={setTheme}
          planMode={planMode}
          onTogglePlanMode={() => setPlanMode((p) => !p)}
          panel={panel}
          onTogglePanel={(p) => setPanel((prev) => (prev === p ? null : p))}
          onOpenHelp={() => openHelp()}
          updateAvailable={updateAvailable}
          onCheckUpdates={() => {
            void runUpdateCheckVisible()
          }}
          onInstallUpdate={() => {
            if (desktopInfo?.update_available && desktopInfo.download_url) {
              setShowUpdateScreen(true)
            } else {
              void runUpdateCheckVisible()
            }
          }}
          timeTravelOpen={timeTravelOpen}
          onToggleTimeTravel={() => setTimeTravelOpen((v) => !v)}
        />

        <UsageDashboard
          open={usageOpen}
          onClose={() => setUsageOpen(false)}
          sessionId={activeId}
          provider={llmProvider}
          model={model}
        />

        {switchToast && (
          <div
            className="fixed bottom-16 left-1/2 -translate-x-1/2 z-50 px-3 py-2 rounded-lg text-xs shadow-lg max-w-[90vw]"
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--accent)',
              color: 'var(--text-primary)',
            }}
            role="status"
          >
            {switchToast}
          </div>
        )}
      </div>
    </div>

    <UserNamePrompt
      open={askUserName && !showSetupWizard}
      initial={userName}
      onSave={(n) => {
        setUserName(n)
        setAskUserName(false)
        void updateSettings({ user_name: n }).catch(() => {})
        try {
          localStorage.removeItem('remedy.userName.skipped')
        } catch {
          /* */
        }
      }}
      onSkip={() => {
        setAskUserName(false)
        try {
          localStorage.setItem('remedy.userName.skipped', '1')
        } catch {
          /* */
        }
      }}
    />

    <QuitServerWarning
      open={quitWarnOpen}
      onCancel={() => setQuitWarnOpen(false)}
      onConfirmQuit={(dont) => {
        void confirmQuitApp(dont)
      }}
    />

    {aboutOpen && (
      <div
        className="fixed inset-0 z-[90] flex items-center justify-center p-4"
        style={{ background: 'rgba(0,0,0,0.55)' }}
        role="dialog"
        aria-modal="true"
        onClick={() => setAboutOpen(false)}
      >
        <div
          className="w-full max-w-sm rounded-xl p-5 shadow-2xl"
          style={{
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <img
            src="/logo.png"
            alt="Remedy"
            style={{ height: 28, width: 'auto', marginBottom: 12 }}
          />
          <div className="text-sm font-semibold mb-1">About Remedy</div>
          <div className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            Your personal AI partner — knowledge, design, code, and get-it-done.
          </div>
          <div className="text-xs space-y-1 mb-4" style={{ color: 'var(--text-secondary)' }}>
            <div>
              Version{' '}
              <span style={{ color: 'var(--accent)' }}>
                {appVersion || updateInfo?.current_version || desktopInfo?.current_version || '—'}
              </span>
            </div>
            {userName && <div>Signed in as {userName}</div>}
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="px-3 py-1.5 rounded-lg text-xs"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
              onClick={() => {
                setAboutOpen(false)
                openHelp('13-whats-new')
              }}
            >
              Help
            </button>
            <button
              type="button"
              className="px-3 py-1.5 rounded-lg text-xs"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
              onClick={() => {
                setAboutOpen(false)
                setPanel('settings')
              }}
            >
              Settings
            </button>
            <button
              type="button"
              className="px-3 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
              onClick={() => setAboutOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    )}
    </AppShell>
  )
}
