import { useState, useCallback, useEffect, useMemo, useRef, lazy, Suspense } from 'react'
import { Sidebar } from './components/Sidebar'
import { ApprovalBanner } from './components/ApprovalBanner'
import { MessageFeed } from './components/MessageFeed'
import { Composer, type ComposerHandle } from './components/Composer'
import { StatusBar, type ThinkingLevel, type ApprovalMode } from './components/StatusBar'
import { WorkspaceSide, PopoutOverlay } from './components/slides/WorkspaceSide'
import { FilesSlide } from './components/slides/FilesSlide'
import { TerminalSlide } from './components/slides/TerminalSlide'
import { BrowserSlide } from './components/slides/BrowserSlide'
import { ScratchSlide } from './components/slides/ScratchSlide'
import {
  loadWorkspaceLayout,
  saveWorkspaceLayout,
  type WorkspaceLayout,
} from './workspace/layoutPrefs'
import { SLIDE_META, type SlideId } from './workspace/types'
import { PlanBanner } from './components/PlanBanner'
import { LibrarySuggestChip } from './components/LibrarySuggestChip'
import { TokenCostTicker } from './components/TokenCostTicker'
import {
  estimateCostUsd,
  estimateTokensText,
  liveRunEstimate,
  type UsageSnapshot,
} from './utils/tokenCost'
import { QuitServerWarning } from './components/QuitServerWarning'
import { SplashScreen } from './components/SplashScreen'
import { TitleBar, type AppMenuAction } from './components/TitleBar'
import { UserNamePrompt } from './components/UserNamePrompt'
import { CommandPalette, type CommandItem } from './components/CommandPalette'
import { ChatSessionHeader } from './components/ChatSessionHeader'
import { AboutDialog } from './components/AboutDialog'

// Heavy / rarely-open surfaces — code-split so first paint stays lean
const SettingsPanel = lazy(() =>
  import('./components/SettingsPanel').then((m) => ({ default: m.SettingsPanel })),
)
const HelpPanel = lazy(() =>
  import('./components/HelpPanel').then((m) => ({ default: m.HelpPanel })),
)
const SetupWizard = lazy(() =>
  import('./components/SetupWizard').then((m) => ({ default: m.SetupWizard })),
)
const UpdateScreen = lazy(() =>
  import('./components/UpdateScreen').then((m) => ({ default: m.UpdateScreen })),
)
const UsageDashboard = lazy(() =>
  import('./components/UsageDashboard').then((m) => ({ default: m.UsageDashboard })),
)
const DiagnosticsPanel = lazy(() =>
  import('./components/DiagnosticsPanel').then((m) => ({ default: m.DiagnosticsPanel })),
)
const TimeTravelTimeline = lazy(() =>
  import('./components/TimeTravelTimeline').then((m) => ({ default: m.TimeTravelTimeline })),
)
const MemoryPanel = lazy(() =>
  import('./components/Panels').then((m) => ({ default: m.MemoryPanel })),
)
const SkillsPanel = lazy(() =>
  import('./components/Panels').then((m) => ({ default: m.SkillsPanel })),
)
import { useSessions } from './hooks/useSessions'
import { useMessages } from './hooks/useMessages'
import { useTheme } from './hooks/useTheme'
import { loadUiMode, saveUiMode, type UiMode } from './utils/uiMode'
import { browserStackSet } from './utils/browserStack'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { useNotifications } from './hooks/useNotifications'
import { useUpdateChecker } from './hooks/useUpdateChecker'
import { useQuitFlow } from './hooks/useQuitFlow'
import { useComputerHost } from './hooks/useComputerHost'
import { useSessionLlm, type ModelInfo } from './hooks/useSessionLlm'
import { useAppOverlays } from './hooks/useAppOverlays'
import { useAppBootstrap } from './hooks/useAppBootstrap'
import { useWorkspaceChrome } from './hooks/useWorkspaceChrome'
import { useChatSendFlow } from './hooks/useChatSendFlow'
import { useSessionStreamJobs } from './sessions/useSessionStreamJobs'
import { shouldConfirmNewTurn } from './sessions/concurrentTurns'
import { ConcurrentTurnDialog } from './components/ConcurrentTurnDialog'
import { getStreamJob, subscribeStreamJobs } from './sessions/streamJobs'
import { listAgents, listCommands, exportSession, importSession } from './api/messages'
import { apiFetch, getServerUrl } from './api/client'
import { getSettings, updateSettings } from './api/settings'
import { listConnectedProviders, setSessionLlm as applySessionLlm } from './api/providers'
import { isPlaceholderTitle, titleFromPrompt } from './utils/sessionTitle'
import { tauriInvoke, tauriListen } from './api/tauri'
import { normalizeToolProcess, type ToolProcessMode } from './utils/toolLabels'
import { looksLikeBuildKick } from './utils/buildKick'
import { HOTKEYS } from './hotkeys'
import type { ShortcutDef } from './hooks/useKeyboardShortcuts'

export type { ModelInfo }

type ServerState = 'connecting' | 'ready' | 'error'

function isTauri(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as any
  return !!(w.__TAURI__ || w.__TAURI_INTERNALS__ || w.isTauri)
}

/** Window shell: OS decorations for min/max/close + in-app menu strip. */
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
        <Suspense fallback={null}>
          <HelpPanel
            open={Boolean(helpOpen)}
            onClose={onCloseHelp}
            initialArticleId={helpArticleId}
            version={version}
          />
        </Suspense>
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
    loadError: messagesLoadError,
    hasOlder: messagesHasOlder,
    loadingOlder: messagesLoadingOlder,
    loadOlder: loadOlderMessages,
    streaming,
    streamStalled,
    stallSeconds,
    stallBannerDismissed,
    dismissStallBanner,
    partialText,
    partialThinking,
    activeTools,
    processSteps,
    taskProgress,
    runUsage,
    queue,
    librarySuggest,
    clearLibrarySuggest,
    send,
    stop,
    stopAndRetry,
    cancelQueued,
    clearQueue,
    updateQueued,
    promoteQueued,
    runCommand,
    clearLocalHistory,
    addCommandMessage,
    beginEdit,
    load: reloadMessages,
  } = useMessages(activeId)
  /** Image viewer → composer attachment rail (markup becomes prompt attachment). */
  const composerRef = useRef<ComposerHandle>(null)
  const handleAttachMarkup = useCallback(async (file: File) => {
    await composerRef.current?.addFiles([file])
    composerRef.current?.focus()
  }, [])

  const {
    themeId,
    theme,
    set: setTheme,
    density,
    setDensity,
    customAccent,
    setCustomAccent,
  } = useTheme()
  const {
    model,
    setModel,
    llmProvider,
    setLlmProvider,
    models,
    setModels,
    connectedProviders,
    setConnectedProviders,
    sessionLlmMap,
    setSessionBind,
    barProvider,
    barModel,
    switchToast,
    setSwitchToast,
    refreshModels,
    onProviderModelChange,
    onModelChange,
  } = useSessionLlm({ activeId, sessions, streaming })

  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('high')
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('ask')
  const [privacyMode, setPrivacyMode] = useState(false)
  const [toolProcessMode, setToolProcessMode] = useState<ToolProcessMode>('off')
  const [uiMode, setUiMode] = useState<UiMode>(() => loadUiMode())
  /** Plan mode is per-session so switching chats does not stick Plan/Build. */
  const [planModeBySession, setPlanModeBySession] = useState<Record<string, boolean>>({})
  const planMode = Boolean(activeId && planModeBySession[activeId])
  const setPlanMode = useCallback(
    (value: boolean | ((prev: boolean) => boolean)) => {
      if (!activeId) return
      setPlanModeBySession((prev) => {
        const cur = Boolean(prev[activeId])
        const next = typeof value === 'function' ? value(cur) : value
        if (cur === next) return prev
        return { ...prev, [activeId]: next }
      })
    },
    [activeId],
  )
  const [panel, setPanel] = useState<'memory' | 'skills' | 'settings' | null>(null)
  const {
    wsLayout,
    setWsLayout,
    popout,
    setPopout,
    patchWs,
    openBrowserInRail,
    swapSides,
  } = useWorkspaceChrome({ setPanel })
  useComputerHost(true, openBrowserInRail)

  /**
   * Settings always open in the **right** workspace rail.
   * Never steals the left (sessions) rail; never uses the floating panel
   * (that used to collapse the chat shell).
   */
  const openSettingsInRail = useCallback(() => {
    setPanel(null)
    setWsLayout((prev) => {
      const next = {
        ...prev,
        // If settings was parked on the left, put sessions back on the left.
        left: prev.left === 'settings' ? ('sessions' as const) : prev.left,
        leftRail:
          prev.left === 'settings'
            ? ('open' as const)
            : prev.leftRail === 'thin'
              ? ('icons' as const)
              : prev.leftRail,
        right: 'settings' as const,
        rightRail: 'open' as const,
        rightOpen: true,
        leftOpen:
          prev.left === 'settings'
            ? true
            : prev.leftRail === 'open' || prev.leftOpen,
      }
      saveWorkspaceLayout(next)
      return next
    })
  }, [])
  /** Track recently opened sessions (for archive filter only — no chip strip UI). */
  const [openTabs, setOpenTabs] = useState<Set<string>>(new Set())
  const openTabIds = useMemo(() => [...openTabs], [openTabs])
  const [serverState, setServerState] = useState<ServerState>(isTauri() ? 'connecting' : 'ready')
  const [serverError, setServerError] = useState('')
  const [agentDefs, setAgentDefs] = useState<{ name: string; description: string }[]>([])
  const { notify } = useNotifications()
  const { busyIds, runningCount } = useSessionStreamJobs()
  // Toast when a background (detached) turn finishes.
  useEffect(() => {
    return subscribeStreamJobs((ev) => {
      if (ev.type !== 'update') return
      const j = ev.job
      if (j.status === 'running' || !j.detached) return
      if (j.sessionId === activeId) return
      const title =
        sessions.find((s) => s.id === j.sessionId)?.title || 'Background session'
      if (j.status === 'done') {
        notify('Turn finished', { body: title, silent: true })
        void refreshSessions()
      } else if (j.status === 'error') {
        notify('Background turn failed', {
          body: j.error ? `${title}: ${j.error}` : title,
        })
      }
    })
  }, [activeId, sessions, notify, refreshSessions])
  const {
    updateInfo,
    desktopInfo,
    checking: checkingUpdates,
    check: checkUpdates,
    lastStatus: updateLastStatus,
    updateAvailable,
  } = useUpdateChecker({ ready: serverState === 'ready' })
  // Always run in Tauri — do not wait for serverState or the poller never starts
  // and navigate looks "offline" forever. Loopback host APIs need no SPA token.
  const [showSetupWizard, setShowSetupWizard] = useState(false)
  const [showUpdateScreen, setShowUpdateScreen] = useState(false)
  const [userName, setUserName] = useState('')
  /** Partner display name (settings.name) for assistant avatar initials */
  const [partnerName, setPartnerName] = useState('Remedy')
  const [askUserName, setAskUserName] = useState(false)
  const [appVersion, setAppVersion] = useState('')
  const {
    aboutOpen,
    setAboutOpen,
    helpOpen,
    setHelpOpen,
    helpArticleId,
    setHelpArticleId,
    usageOpen,
    setUsageOpen,
    diagnosticsOpen,
    setDiagnosticsOpen,
    paletteOpen,
    setPaletteOpen,
    timeTravelOpen,
    setTimeTravelOpen,
    quitWarnOpen,
    setQuitWarnOpen,
    openHelp,
  } = useAppOverlays()
  const sessionUsage: UsageSnapshot = useMemo(() => {
    let prompt = 0
    let completion = 0
    for (const m of messages) {
      if (m.reverted) continue
      if (m.role === 'user') {
        prompt += estimateTokensText(m.content || '')
      } else if (m.role === 'assistant') {
        if (typeof m.tokens === 'number' && m.tokens > 0) {
          completion += m.tokens
        } else {
          completion += estimateTokensText(
            `${m.content || ''}${m.thinking || ''}`,
          )
        }
      }
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
  }, [messages, model, llmProvider])

  /** Live run ticker: provider usage when present, else partial-token estimate. */
  const displayRunUsage = useMemo(
    () =>
      liveRunEstimate(
        partialText,
        partialThinking,
        model,
        llmProvider,
        runUsage,
      ),
    [partialText, partialThinking, model, llmProvider, runUsage],
  )

  const { confirmQuitApp, requestQuitWithWarning } = useQuitFlow({ setQuitWarnOpen })


  /** Run update check with visible UI (settings About section), not a silent focus. */
  const runUpdateCheckVisible = useCallback(async () => {
    openSettingsInRail()
    const result = await checkUpdates()
    if (result.updateAvailable && result.desktopInfo?.download_url) {
      setShowUpdateScreen(true)
    }
    return result
  }, [checkUpdates, openSettingsInRail])

  const handleMenuAction = useCallback(
    (action: AppMenuAction) => {
      switch (action) {
        case 'settings':
          openSettingsInRail()
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
        case 'diagnostics':
          setDiagnosticsOpen(true)
          break
        case 'switch_web_ui':
          void (async () => {
            if (!isTauri()) {
              // Browser already — open API web UI in a new tab
              window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
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
                await openExternalUrl(getServerUrl() + '/')
              } catch {
                window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
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
    [runUpdateCheckVisible, desktopInfo, create, openHelp, requestQuitWithWarning, openSettingsInRail],
  )

  // Tray Quit / window close when not hide-to-tray → show server-stop warning
  useEffect(() => {
    if (!isTauri()) return
    let off: (() => void) | undefined
    let cancelled = false
    // Hydrate "don't show again" from disk → localStorage (survives WebView clears less often)
    void (async () => {
      try {
        const prefs = await tauriInvoke<{ skip_quit_server_warning?: boolean }>(
          'get_desktop_prefs',
        )
        if (prefs?.skip_quit_server_warning) {
          localStorage.setItem('remedy.skipQuitServerWarning', '1')
        }
      } catch {
        /* */
      }
    })()
    void tauriListen('app-quit-requested', () => {
      // Rust only emits when skip is false on disk; still respect localStorage race.
      try {
        if (localStorage.getItem('remedy.skipQuitServerWarning') === '1') {
          void tauriInvoke('quit_app').catch(() => setQuitWarnOpen(true))
          return
        }
      } catch {
        /* */
      }
      setQuitWarnOpen(true)
    }).then((fn) => {
      if (cancelled) {
        fn()
        return
      }
      off = fn
    })
    return () => {
      cancelled = true
      off?.()
    }
  }, [])

  useEffect(() => {
    if (!isTauri()) return
    let off: Array<() => void> = []
    let cancelled = false
    void (async () => {
      const a = await tauriListen('server-ready', () => {
        setServerState('ready')
      })
      const b = await tauriListen('server-error', (payload) => {
        setServerState('error')
        setServerError(typeof payload === 'string' ? payload : 'Server failed to start')
      })
      if (cancelled) {
        a()
        b()
        return
      }
      off.push(a, b)
    })()
    return () => {
      cancelled = true
      for (const u of off) u()
    }
  }, [])

  // Tray menu → themed in-app panels (must open Settings, not just focus chat)
  useEffect(() => {
    if (!isTauri()) return
    let off: Array<() => void> = []
    let cancelled = false
    void (async () => {
      const listeners = await Promise.all([
        tauriListen('tray-open-settings', () => openSettingsInRail()),
        tauriListen('tray-check-updates', () => {
          void runUpdateCheckVisible()
        }),
        tauriListen('tray-about', () => setAboutOpen(true)),
      ])
      if (cancelled) {
        for (const u of listeners) u()
        return
      }
      off = listeners
    })()
    return () => {
      cancelled = true
      for (const u of off) u()
    }
  }, [runUpdateCheckVisible, openSettingsInRail])


  useAppBootstrap({
    serverState,
    refreshSessions,
    refreshModels,
    setModel,
    setLlmProvider,
    setConnectedProviders,
    setThinkingLevel,
    setApprovalMode,
    setPrivacyMode,
    setToolProcessMode,
    setUserName,
    setPartnerName,
    setAppVersion,
    setAskUserName,
    setShowSetupWizard,
    setServerError,
    setAgentDefs,
  })

  // Realtime session sync (messenger + multi-surface). Stable subscription —
  // do not re-open SSE when activeId changes (that froze / fought the UI).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId
  const refreshSessionsRef = useRef(refreshSessions)
  refreshSessionsRef.current = refreshSessions
  const reloadMessagesRef = useRef(reloadMessages)
  reloadMessagesRef.current = reloadMessages
  // Avoid force-reloading the active thread while a local stream is mid-flight —
  // that fought partialText and felt like "sync stuck" / catch-up thrash.
  const streamingRefForSync = useRef(streaming)
  streamingRefForSync.current = streaming

  useEffect(() => {
    if (serverState !== 'ready') return
    let unsub: (() => void) | undefined
    let cancelled = false
    let debounce: ReturnType<typeof setTimeout> | null = null
    const scheduleRefresh = (sessionId?: string) => {
      if (debounce) clearTimeout(debounce)
      debounce = setTimeout(() => {
        void refreshSessionsRef.current()
        // load() takes { force }, not session id — force-refresh active thread only
        // when we are not streaming that thread (messenger inbound mid-desktop turn).
        if (
          sessionId
          && sessionId === activeIdRef.current
          && !streamingRefForSync.current
        ) {
          void reloadMessagesRef.current({ force: true })
        }
      }, 400)
    }
    void import('./api/sessionEvents').then(({ subscribeSessionEvents }) => {
      if (cancelled) return
      unsub = subscribeSessionEvents({
        onEvent: (ev) => {
          if (
            ev.type === 'session_created'
            || ev.type === 'session_updated'
            || ev.type === 'message_added'
            || ev.type === 'session_deleted'
          ) {
            scheduleRefresh(ev.session_id)
          }
        },
      })
    })
    return () => {
      cancelled = true
      if (debounce) clearTimeout(debounce)
      unsub?.()
    }
  }, [serverState])

  const handleNewSession = useCallback(async () => {
    // Stamp with *current bar* bind (active session), not a floating global.
    const prov = barProvider
    const mid = barModel
    const s = await create(undefined, { provider: prov, model: mid })
    if (s) {
      setOpenTabs((prev) => new Set([...prev, s.id]))
      setSessionBind(
        s.id,
        String(s.llm_provider || prov),
        String(s.model || mid),
      )
    }
  }, [create, barProvider, barModel, setSessionBind])

  const handleSelect = useCallback(
    (id: string) => {
      if (!id) return
      // Always set active — useMessages force-loads history on session change.
      setActiveId(id)
      setOpenTabs((prev) => {
        if (prev.has(id)) return prev
        return new Set([...prev, id])
      })
      // Keep sessions panel open so selection is obvious
      setWsLayout((prev) => {
        if (prev.left === 'sessions' && prev.leftRail !== 'open') {
          const next = { ...prev, leftRail: 'open' as const, leftOpen: true }
          saveWorkspaceLayout(next)
          return next
        }
        return prev
      })
    },
    [setActiveId],
  )

  const handleCloseTab = useCallback(
    (id: string) => {
      setOpenTabs((prev) => {
        const next = new Set(prev)
        next.delete(id)
        if (activeId === id) {
          if (next.size > 0) {
            // Prefer neighbor in recency order (sessions list is newest-first-ish)
            const ordered = sessions
              .map((s) => s.id)
              .filter((sid) => next.has(sid))
            const fromOpen = [...next]
            const pick = ordered[0] || fromOpen[fromOpen.length - 1]!
            setActiveId(pick)
          } else {
            // Fall back to any remaining session, not a blank middle
            const fallback = sessions.find((s) => s.id !== id)?.id ?? null
            setActiveId(fallback)
            if (fallback) next.add(fallback)
          }
        }
        return next
      })
    },
    [activeId, setActiveId, sessions],
  )

  const handleExport = useCallback(
    async (sessionId: string) => {
      notify('Preparing export…', { silent: true })
      // Yield so the UI can paint before heavy work (large sessions).
      await new Promise<void>((r) => window.requestAnimationFrame(() => r()))
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
        )
          .replace(/[<>:"/\\|?*]/g, '_')
          // Strip C0 controls without a control-class regex (oxlint no-control-regex).
          .replace(/./g, (ch) => (ch.charCodeAt(0) < 32 ? '_' : ch))

        await new Promise<void>((r) => window.requestAnimationFrame(() => r()))

        // Tauri: native rfd Save dialog (no PowerShell cold-start).
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
      // Desktop: native open dialog + Rust read (no WebView FileReader / path-jail lag).
      if (isTauri()) {
        try {
          const picked = await tauriInvoke<{
            path: string
            text: string
            name?: string
          } | null>('open_text_file')
          if (!picked) return
          notify('Importing session…', { silent: true })
          await new Promise<void>((r) => window.requestAnimationFrame(() => r()))
          const stem = (picked.name || picked.path)
            .replace(/^.*[\\/]/, '')
            .replace(/\.(txt|md)$/i, '')
            .trim()
          const title =
            stem && !stem.toLowerCase().startsWith('remedy-export') ? stem : undefined
          // Prefer text body (always works); path is optional fallback for API.
          const created = await importSession({
            text: picked.text,
            title,
          })
          await refreshSessions()
          if (created?.id) {
            setActiveId(created.id)
            setOpenTabs((prev) => new Set([...prev, created.id]))
            notify('Session imported', {
              body: created.title || `${created.imported_messages ?? ''} messages`,
              silent: true,
            })
          }
          return
        } catch (nativeErr) {
          console.warn('Native open failed, falling back to file input:', nativeErr)
        }
      }

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
      notify('Importing session…', { silent: true })
      await new Promise<void>((r) => window.requestAnimationFrame(() => r()))
      const text = await file.text()
      if (!text.trim()) {
        notify('Import failed', { body: 'File is empty' })
        return
      }
      // Guard absurd imports that freeze the process
      if (text.length > 8_000_000) {
        notify('Import failed', {
          body: 'File too large (8 MB max). Export without embedded images or split the session.',
        })
        return
      }
      const stem = file.name.replace(/\.(txt|md)$/i, '').trim()
      const title =
        stem && !stem.toLowerCase().startsWith('remedy-export') ? stem : undefined
      await new Promise<void>((r) => window.requestAnimationFrame(() => r()))
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

  const {
    editDraft,
    setEditDraft,
    concurrentConfirm,
    setConcurrentConfirm,
    skipConcurrentConfirmRef,
    handleCommand,
    handleSend,
    handleEditUserMessage,
    handleRegenerate,
  } = useChatSendFlow({
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
  })

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
      { id: 'settings', label: 'Settings Panel', description: 'Open settings in the right rail', category: 'panel', action: () => openSettingsInRail() },
      {
        id: 'help',
        label: "Help / Owner's Manual",
        description: 'Searchable offline wiki (F1 / Ctrl+/)',
        category: 'general',
        action: () => openHelp(),
      },
      {
        id: 'diagnostics',
        label: 'Health Diagnostics',
        description: 'Remedy server, RMB, hardware, cloud providers',
        category: 'panel',
        action: () => setDiagnosticsOpen(true),
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
    handleExport,
    handleImport,
    activeId,
    openHelp,
    openSettingsInRail,
    setPlanMode,
  ])

  // Wire global shortcuts from hotkeys.ts (single source of truth for labels + keys).
  const globalShortcuts: ShortcutDef[] = useMemo(() => {
    const togglePlan = () => setPlanMode((p) => !p)
    const byAction: Record<string, () => void> = {
      'New chat session': () => {
        void handleNewSession()
      },
      'Open command palette': () => setPaletteOpen((o) => !o),
      'Toggle plan mode': togglePlan,
      'Open settings': () => openSettingsInRail(),
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
      // Plan toggle + help must work while the composer (textarea) is focused.
      const allowInInput =
        h.match.key === 'F1'
        || h.match.key === 'Escape'
        || h.match.key === 'Tab'
        || (h.match.key === 'b' && h.match.ctrl)
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
  }, [handleNewSession, openHelp, helpOpen, openSettingsInRail, setPlanMode])

  useKeyboardShortcuts(globalShortcuts)

  // Native Browser embed HWND paints above React — hide while full-window overlays open.
  useEffect(() => {
    browserStackSet('help', helpOpen)
    browserStackSet('palette', paletteOpen)
    browserStackSet('about', aboutOpen)
    browserStackSet('quit-warn', quitWarnOpen)
    browserStackSet('time-travel', timeTravelOpen)
    browserStackSet('usage', usageOpen)
    browserStackSet('diagnostics', diagnosticsOpen)
    browserStackSet('concurrent-turn', Boolean(concurrentConfirm))
    browserStackSet('ask-user-name', askUserName)
    return () => {
      browserStackSet('help', false)
      browserStackSet('palette', false)
      browserStackSet('about', false)
      browserStackSet('quit-warn', false)
      browserStackSet('time-travel', false)
      browserStackSet('usage', false)
      browserStackSet('diagnostics', false)
      browserStackSet('concurrent-turn', false)
      browserStackSet('ask-user-name', false)
    }
  }, [
    helpOpen,
    paletteOpen,
    aboutOpen,
    quitWarnOpen,
    timeTravelOpen,
    usageOpen,
    diagnosticsOpen,
    concurrentConfirm,
    askUserName,
  ])

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
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--text-muted)' }}>
              Loading setup…
            </div>
          }
        >
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
        </Suspense>
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
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--text-muted)' }}>
              Loading update…
            </div>
          }
        >
          <UpdateScreen
            info={desktopInfo}
            autoStart
            onClose={() => setShowUpdateScreen(false)}
          />
        </Suspense>
      </AppShell>
    )
  }

  const sessionsSlide = (
    <Sidebar
      embedded
      sessions={sessions}
      activeId={activeId}
      busySessionIds={busyIds}
      onSelect={handleSelect}
      onNew={handleNewSession}
      onNewInProject={(projectPath, opts) => {
        void (async () => {
          // New project session starts with whatever is on the bar *now*;
          // user can switch provider immediately — map entry is sticky after that.
          const prov = barProvider
          const mid = barModel
          const s = await createInProject(projectPath, undefined, {
            ...opts,
            llm: { provider: prov, model: mid },
          })
          if (s?.id) {
            setOpenTabs((prev) => new Set([...prev, s.id]))
            setSessionBind(
              s.id,
              String(s.llm_provider || prov),
              String(s.model || mid),
            )
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
        if (!isTauri()) {
          const typed = window.prompt('Project folder path')
          return typed && typed.trim() ? typed.trim() : null
        }
        const path = await tauriInvoke<string | null>('pick_folder')
        return path && path.trim() ? path.trim() : null
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
      openTabIds={openTabIds}
      footer={
        <TokenCostTicker
          placement="sidebar"
          run={displayRunUsage}
          session={sessionUsage}
          streaming={streaming}
          model={barModel}
          provider={barProvider}
        />
      }
    />
  )

  const renderSlide = (id: SlideId) => {
    switch (id) {
      case 'sessions':
        return sessionsSlide
      case 'settings':
        return (
          <Suspense fallback={<div className="p-4 text-xs" style={{ color: 'var(--text-muted)' }}>Loading settings…</div>}>
          <SettingsPanel
            open
            embedded
            onClose={() => {}}
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
                  if (s.llm_provider && s.llm_model) {
                    if (activeId) {
                      setSessionBind(
                        activeId,
                        String(s.llm_provider),
                        String(s.llm_model),
                      )
                      void applySessionLlm(
                        activeId,
                        String(s.llm_provider),
                        String(s.llm_model),
                        true,
                      ).catch(() => {})
                    } else {
                      setLlmProvider(String(s.llm_provider))
                      setModel(String(s.llm_model))
                    }
                  }
                  setUserName((s.user_name || '').trim())
                  setToolProcessMode(normalizeToolProcess(s.tool_process))
                  setPrivacyMode(Boolean(s.privacy_mode))
                  setApprovalMode(
                    String(s.approval_mode || 'ask').toLowerCase() === 'auto' ? 'auto' : 'ask',
                  )
                  return refreshModels({
                    provider: s.llm_provider ? String(s.llm_provider) : undefined,
                  })
                })
                .catch(() => refreshModels())
            }}
          />
          </Suspense>
        )
      case 'files':
        return <FilesSlide sessionId={activeId} />
      case 'terminal':
        return <TerminalSlide sessionId={activeId} />
      case 'browser':
        return <BrowserSlide />
      case 'scratch':
        return <ScratchSlide sessionId={activeId} />
      default:
        return null
    }
  }

  return (
    <AppShell {...shellProps}>
    <div className="flex flex-col flex-1 min-h-0" style={{ background: 'var(--bg-primary)' }}>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={paletteCommands}
      />

      {/* True three-column shell: Left | Chat | Right (rails on outer edges) */}
      <div className="flex flex-1 min-h-0 h-full items-stretch">
        <WorkspaceSide
          side="left"
          active={wsLayout.left}
          width={wsLayout.leftWidth}
          railMode={wsLayout.leftRail}
          onSelect={(id) => patchWs({ left: id })}
          onWidth={(w) => patchWs({ leftWidth: w })}
          onRailMode={(mode) =>
            patchWs({
              leftRail: mode,
              leftOpen: mode === 'open',
            })
          }
          onSwap={swapSides}
          onPopout={
            SLIDE_META[wsLayout.left]?.popout
              ? () => setPopout({ id: wsLayout.left, fullscreen: false })
              : undefined
          }
          onFullscreen={
            SLIDE_META[wsLayout.left]?.popout
              ? () => setPopout({ id: wsLayout.left, fullscreen: true })
              : undefined
          }
        >
          {/* Unmount when this slide is in popout — avoids dual Browser bounds / dual PTYs */}
          {wsLayout.leftRail === 'open' && popout?.id !== wsLayout.left
            ? renderSlide(wsLayout.left)
            : null}
        </WorkspaceSide>

        {/* Middle: CSS grid rows auto/1fr/auto — flex was collapsing the message feed to 0px. */}
        <div
          className="chat-middle"
          style={{
            display: 'grid',
            gridTemplateRows: 'auto auto minmax(0, 1fr) auto',
            flex: '1 1 0%',
            minWidth: 240,
            minHeight: 0,
            height: '100%',
            alignSelf: 'stretch',
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          <ChatSessionHeader
            title={
              (activeId
                && sessions.find((s) => s.id === activeId)?.title)
              || 'New chat'
            }
            partnerName={partnerName}
            modelLabel={barModel || model}
            providerLabel={barProvider || llmProvider}
            planMode={planMode}
            streaming={streaming}
            messageCount={
              activeId
                ? sessions.find((s) => s.id === activeId)?.message_count
                : messages.length
            }
            onTogglePlanMode={() => setPlanMode((p) => !p)}
          />

          <div style={{ position: 'relative', zIndex: 6 }}>
            <ApprovalBanner sessionId={activeId} />
            <PlanBanner
              planMode={planMode}
              sessionId={activeId}
              onApproveBuild={() => {
                // Leave Plan mode → Build. Banner hides once status is
                // approved/active so the plan card does not stick over chat
                // while implementation is in motion (re-open Plan via Ctrl+B).
                setPlanMode(false)
                setEditDraft({
                  text: 'Implement the approved plan. Follow the saved steps carefully.',
                  key: Date.now(),
                })
              }}
              onRequestChanges={(hint) => {
                setPlanMode(true)
                setEditDraft({ text: hint, key: Date.now() })
              }}
              onCancelled={() => {
                // Durable cancel already persisted; leave Plan mode so tools unlock.
                setPlanMode(false)
              }}
            />
          </div>

          <div
            className="chat-middle-feed"
            style={{
              minHeight: 0,
              overflow: 'hidden',
              position: 'relative',
            }}
          >
            <MessageFeed
              messages={messages}
              partialText={partialText}
              partialThinking={partialThinking}
              streaming={streaming}
              loading={messagesLoading}
              loadError={messagesLoadError}
              planMode={planMode}
              activeTools={activeTools}
              processSteps={processSteps}
              taskProgress={taskProgress}
              toolProcessMode={toolProcessMode}
              onEditUserMessage={handleEditUserMessage}
              onQuickPrompt={(text) => void handleSend(text)}
              onRegenerate={(id) => void handleRegenerate(id)}
              userName={userName}
              partnerName={partnerName}
              onAttachMarkup={handleAttachMarkup}
              hasOlder={messagesHasOlder}
              loadingOlder={messagesLoadingOlder}
              onLoadOlder={() => void loadOlderMessages()}
              projectPath={
                (activeId && sessions.find((s) => s.id === activeId)?.project_path)
                || null
              }
            />
          </div>

          <div
            className="chat-middle-composer flex flex-col"
            style={{ position: 'relative', zIndex: 5 }}
          >
            {!streaming
              && activeId
              && getStreamJob(activeId)?.status === 'running' && (
              <div className="chat-status-banner chat-status-banner-accent" role="status">
                <span className="flex-1 min-w-[12rem]">
                  Still working in the background — switch away anytime; Stop
                  ends this session&apos;s turn only.
                </span>
                <button
                  type="button"
                  className="chat-status-btn chat-status-btn-danger"
                  onClick={() => stop()}
                >
                  Stop
                </button>
              </div>
            )}
            {streaming && streamStalled && !stallBannerDismissed && (
              <div className="chat-status-banner chat-status-banner-muted" role="status">
                <span className="flex-1 min-w-[12rem]">
                  Quiet for {stallSeconds}s — model may still be thinking. You can keep waiting
                  or stop if nothing new arrives.
                </span>
                <button
                  type="button"
                  className="chat-status-btn chat-status-btn-ghost"
                  onClick={() => dismissStallBanner()}
                  title="Hide this notice for the rest of this turn"
                >
                  Hide
                </button>
                <button
                  type="button"
                  className="chat-status-btn chat-status-btn-danger"
                  onClick={() => stop()}
                >
                  Stop
                </button>
                <button
                  type="button"
                  className="chat-status-btn chat-status-btn-primary"
                  onClick={() => stopAndRetry()}
                  title="Abort this turn and send the same prompt again"
                >
                  Stop &amp; retry
                </button>
              </div>
            )}
            <TokenCostTicker
              placement="sidebar"
              run={displayRunUsage}
              session={sessionUsage}
              streaming={streaming}
              model={barModel}
              provider={barProvider}
            />
            <LibrarySuggestChip
              suggestion={librarySuggest}
              sessionId={activeId}
              onDismiss={clearLibrarySuggest}
              onOpenLibrary={() => {
                setPanel('skills')
              }}
              onInstalled={() => {
                // Open Skills so the pack is visible; chip auto-clears after success
                setPanel('skills')
              }}
            />
            <Composer
              ref={composerRef}
              onSend={handleSend}
              onStop={stop}
              onCommand={handleCommand}
              streaming={streaming}
              queue={queue}
              onCancelQueued={cancelQueued}
              onClearQueue={clearQueue}
              onPromoteQueued={promoteQueued}
              onUpdateQueued={updateQueued}
              disabled={serverState !== 'ready'}
              planMode={planMode}
              onTogglePlanMode={() => setPlanMode((p) => !p)}
              agents={agentDefs}
              editDraft={editDraft}
              sessionId={activeId}
              llmProvider={llmProvider}
              llmModel={model}
              onOpenSettings={openSettingsInRail}
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
        </div>

        <WorkspaceSide
          side="right"
          active={wsLayout.right}
          width={wsLayout.rightWidth}
          railMode={wsLayout.rightRail}
          onSelect={(id) => patchWs({ right: id })}
          onWidth={(w) => patchWs({ rightWidth: w })}
          onRailMode={(mode) =>
            patchWs({
              rightRail: mode,
              rightOpen: mode === 'open',
            })
          }
          onSwap={swapSides}
          onPopout={
            SLIDE_META[wsLayout.right]?.popout
              ? () => setPopout({ id: wsLayout.right, fullscreen: false })
              : undefined
          }
          onFullscreen={
            SLIDE_META[wsLayout.right]?.popout
              ? () => setPopout({ id: wsLayout.right, fullscreen: true })
              : undefined
          }
        >
          {wsLayout.rightRail === 'open' && popout?.id !== wsLayout.right
            ? renderSlide(wsLayout.right)
            : null}
        </WorkspaceSide>

        {/* Shared by Terminal / Browser / Scratch — same exit chrome + Esc */}
        {popout && (
          <PopoutOverlay
            title={(SLIDE_META[popout.id] ?? SLIDE_META.sessions).label}
            fullscreen={popout.fullscreen}
            onClose={() => setPopout(null)}
            onToggleFullscreen={() =>
              setPopout((p) => (p ? { ...p, fullscreen: !p.fullscreen } : null))
            }
          >
            {renderSlide(popout.id)}
          </PopoutOverlay>
        )}
      </div>

      {/* Overlays outside three-column flex so they never collapse chat feed height */}
      <Suspense fallback={null}>
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
      {/* Floating settings disabled — always use right rail (openSettingsInRail). */}
      <SettingsPanel
        open={false}
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
              // Settings Save = global default + active session only.
              if (s.llm_provider && s.llm_model) {
                if (activeId) {
                  setSessionBind(
                    activeId,
                    String(s.llm_provider),
                    String(s.llm_model),
                  )
                  void applySessionLlm(
                    activeId,
                    String(s.llm_provider),
                    String(s.llm_model),
                    true,
                  ).catch(() => {})
                } else {
                  setLlmProvider(String(s.llm_provider))
                  setModel(String(s.llm_model))
                }
              }
              setUserName((s.user_name || '').trim())
              setToolProcessMode(normalizeToolProcess(s.tool_process))
              setPrivacyMode(Boolean(s.privacy_mode))
              setApprovalMode(
                String(s.approval_mode || 'ask').toLowerCase() === 'auto' ? 'auto' : 'ask',
              )
              return refreshModels({
                provider: s.llm_provider ? String(s.llm_provider) : undefined,
              })
            })
            .catch(() => refreshModels())
        }}
      />
      </Suspense>

      <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={barModel}
          models={models}
          uiMode={uiMode}
          onUiModeChange={(mode) => {
            setUiMode(mode)
            saveUiMode(mode)
          }}
          provider={barProvider}
          connectedProviders={connectedProviders}
          onProviderModelChange={onProviderModelChange}
          onModelChange={onModelChange}
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
          privacyMode={privacyMode}
          onPrivacyModeChange={(on) => {
            setPrivacyMode(on)
            updateSettings({ privacy_mode: on }).catch(() => {})
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
          panel={
            wsLayout.right === 'settings' && wsLayout.rightRail === 'open'
              ? 'settings'
              : panel === 'settings'
                ? null
                : panel
          }
          onTogglePanel={(p) => {
            if (p === 'settings') {
              // Toggle right-rail settings; never collapse chat.
              setPanel(null)
              setWsLayout((prev) => {
                const already =
                  prev.right === 'settings' && prev.rightRail === 'open'
                if (already) {
                  const next = {
                    ...prev,
                    rightRail: 'thin' as const,
                    rightOpen: false,
                  }
                  saveWorkspaceLayout(next)
                  return next
                }
                const next = {
                  ...prev,
                  left: prev.left === 'settings' ? ('sessions' as const) : prev.left,
                  leftRail:
                    prev.left === 'settings' ? ('open' as const) : prev.leftRail,
                  right: 'settings' as const,
                  rightRail: 'open' as const,
                  rightOpen: true,
                }
                saveWorkspaceLayout(next)
                return next
              })
              return
            }
            setPanel((prev) => (prev === p ? null : p))
          }}
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

        <Suspense fallback={null}>
          <UsageDashboard
            open={usageOpen}
            onClose={() => setUsageOpen(false)}
            sessionId={activeId}
            provider={barProvider}
            model={barModel}
          />
        </Suspense>

        <Suspense fallback={null}>
          <DiagnosticsPanel
            open={diagnosticsOpen}
            onClose={() => setDiagnosticsOpen(false)}
          />
        </Suspense>

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

    <ConcurrentTurnDialog
      open={Boolean(concurrentConfirm)}
      runningCount={runningCount}
      onCancel={() => setConcurrentConfirm(null)}
      onContinue={() => {
        const pending = concurrentConfirm
        setConcurrentConfirm(null)
        if (!pending) return
        skipConcurrentConfirmRef.current = true
        void handleSend(pending.text, pending.attachments, pending.opts)
      }}
    />

    <AboutDialog
      open={aboutOpen}
      onClose={() => setAboutOpen(false)}
      version={
        appVersion || updateInfo?.current_version || desktopInfo?.current_version || undefined
      }
      userName={userName}
      onOpenHelp={openHelp}
      onOpenSettings={openSettingsInRail}
      onOpenDiagnostics={() => setDiagnosticsOpen(true)}
    />
    </AppShell>
  )
}
