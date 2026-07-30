import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { Sidebar } from './components/Sidebar'
import { ApprovalBanner } from './components/ApprovalBanner'
import { MessageFeed } from './components/MessageFeed'
import { Composer, type ComposerHandle } from './components/Composer'
import { StatusBar, type ThinkingLevel, type ApprovalMode } from './components/StatusBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { SettingsPanel } from './components/SettingsPanel'
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
import { TimeTravelTimeline } from './components/TimeTravelTimeline'
import {
  estimateCostUsd,
  estimateTokensText,
  liveRunEstimate,
  type UsageSnapshot,
} from './utils/tokenCost'
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
import { loadUiMode, saveUiMode, type UiMode } from './utils/uiMode'
import { browserStackSet } from './utils/browserStack'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { useNotifications } from './hooks/useNotifications'
import { useUpdateChecker } from './hooks/useUpdateChecker'
import { useComputerHost } from './hooks/useComputerHost'
import { useSessionStreamJobs } from './sessions/useSessionStreamJobs'
import { shouldConfirmNewTurn } from './sessions/concurrentTurns'
import { ConcurrentTurnDialog } from './components/ConcurrentTurnDialog'
import { getStreamJob, subscribeStreamJobs } from './sessions/streamJobs'
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
    loadError: messagesLoadError,
    hasOlder: messagesHasOlder,
    loadingOlder: messagesLoadingOlder,
    loadOlder: loadOlderMessages,
    streaming,
    streamStalled,
    stallSeconds,
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
    addCommandMessage,
    beginEdit,
    load: reloadMessages,
  } = useMessages(activeId)
  /** Prefill for edit-and-resend; `key` forces re-apply even for identical text. */
  const [editDraft, setEditDraft] = useState<{ text: string; key: number } | null>(null)
  /** Image viewer → composer attachment rail (markup becomes prompt attachment). */
  const composerRef = useRef<ComposerHandle>(null)
  const handleAttachMarkup = useCallback(async (file: File) => {
    await composerRef.current?.addFiles([file])
    composerRef.current?.focus()
  }, [])

  const [wsLayout, setWsLayout] = useState<WorkspaceLayout>(() => loadWorkspaceLayout())
  const [popout, setPopout] = useState<{
    id: SlideId
    fullscreen: boolean
  } | null>(null)

  const patchWs = useCallback((patch: Partial<WorkspaceLayout>) => {
    setWsLayout((prev) => {
      const next = { ...prev, ...patch }
      // Single WebView2 embed: only one rail may host Browser at a time
      if (next.left === 'browser' && next.right === 'browser') {
        if (patch.left === 'browser') {
          next.right = prev.right === 'browser' ? 'files' : prev.right
        } else if (patch.right === 'browser') {
          next.left = prev.left === 'browser' ? 'files' : prev.left
        } else {
          // layout restore / bulk patch — prefer left browser, move right
          next.right = 'files'
        }
      }
      saveWorkspaceLayout(next)
      return next
    })
  }, [])

  /**
   * Open Browser workspace rail the same way Settings opens — user does not
   * need the rail already visible. Agent computer-use calls this.
   */
  const openBrowserInRail = useCallback(() => {
    setPanel(null)
    setWsLayout((prev) => {
      const next: WorkspaceLayout = {
        ...prev,
        // Prefer right rail (sessions stay left), mirror openSettingsInRail.
        left: prev.left === 'browser' ? prev.left : prev.left,
        right: 'browser',
        rightOpen: true,
        rightRail: 'open',
        // Wider rail so WebView is readable (agent + human)
        rightWidth: Math.max(prev.rightWidth || 0, 440),
        leftOpen: prev.leftRail === 'open' || prev.leftOpen,
      }
      if (next.left === 'browser') {
        next.left = 'sessions'
        next.leftRail = 'open'
        next.leftOpen = true
      }
      saveWorkspaceLayout(next)
      return next
    })
    // After layout paint, ask BrowserSlide host to push bounds (via custom event)
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent('remedy:browser-resync-bounds'))
    }, 80)
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent('remedy:browser-resync-bounds'))
    }, 320)
  }, [])

  // Computer use: open Browser rail (SPA event + Rust host emit).
  useEffect(() => {
    const onComputerUi = (ev: Event) => {
      const detail = (ev as CustomEvent<{ openBrowser?: boolean }>).detail
      if (!detail?.openBrowser) return
      openBrowserInRail()
    }
    window.addEventListener('remedy:computer-ui', onComputerUi)
    let unlisten: (() => void) | undefined
    void import('./api/tauri').then(async ({ isTauri, tauriListen }) => {
      if (!isTauri()) return
      try {
        unlisten = await tauriListen('computer-open-browser', (ev) => {
          openBrowserInRail()
          // Sync address bar via DOM event for BrowserSlide
          const payload = (ev as { payload?: { url?: string } })?.payload
          const u = payload?.url
          if (u) {
            window.dispatchEvent(
              new CustomEvent('remedy:browser-set-url', { detail: { url: u } }),
            )
          }
        })
        const unlistenUrl = await tauriListen('computer-browser-url', (ev) => {
          const payload = (ev as { payload?: { url?: string } })?.payload
          const u = payload?.url
          if (u) {
            window.dispatchEvent(
              new CustomEvent('remedy:browser-set-url', { detail: { url: u } }),
            )
          }
        })
        const prev = unlisten
        unlisten = () => {
          prev?.()
          unlistenUrl?.()
        }
      } catch {
        /* older shell */
      }
    })
    return () => {
      window.removeEventListener('remedy:computer-ui', onComputerUi)
      unlisten?.()
    }
  }, [openBrowserInRail])

  const swapSides = useCallback(() => {
    setWsLayout((prev) => {
      const next: WorkspaceLayout = {
        ...prev,
        left: prev.right,
        right: prev.left,
        leftWidth: prev.rightWidth,
        rightWidth: prev.leftWidth,
        leftOpen: prev.rightOpen,
        rightOpen: prev.leftOpen,
        leftRail: prev.rightRail,
        rightRail: prev.leftRail,
      }
      saveWorkspaceLayout(next)
      return next
    })
  }, [])
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
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [agentDefs, setAgentDefs] = useState<{ name: string; description: string }[]>([])
  const { notify } = useNotifications()
  const { busyIds, runningCount } = useSessionStreamJobs()
  const [concurrentConfirm, setConcurrentConfirm] = useState<{
    text: string
    attachments?: {
      path: string
      name?: string
      mime?: string
      size?: number
      is_image?: boolean
      is_text?: boolean
    }[]
    opts?: { mode?: 'after' | 'interrupt' }
  } | null>(null)
  const skipConcurrentConfirmRef = useRef(false)

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
  useComputerHost(true, openBrowserInRail)
  const [showSetupWizard, setShowSetupWizard] = useState(false)
  const [showUpdateScreen, setShowUpdateScreen] = useState(false)
  const [userName, setUserName] = useState('')
  /** Partner display name (settings.name) for assistant avatar initials */
  const [partnerName, setPartnerName] = useState('Remedy')
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
  /** After a status-bar pick, ignore background settings/session overwrites briefly. */
  const llmUserPinRef = useRef<{ provider: string; model: string; until: number } | null>(
    null,
  )
  const pinUserLlm = useCallback((provider: string, model: string, ms = 45_000) => {
    llmUserPinRef.current = {
      provider,
      model,
      until: Date.now() + ms,
    }
  }, [])

  // Hydrate session LLM map from server — keep stable identity when unchanged
  // so we do not re-fire tab restore and thrash status-bar provider/model.
  // While the user pin is active, never overwrite the pinned session entry.
  useEffect(() => {
    setSessionLlmMap((prev) => {
      let changed = false
      const next = { ...prev }
      const pin = llmUserPinRef.current
      const pinLive = pin && Date.now() <= pin.until
      for (const s of sessions) {
        if (s.llm_provider && s.model) {
          if (
            pinLive
            && activeId
            && s.id === activeId
            && (s.llm_provider !== pin.provider || s.model !== pin.model)
          ) {
            // Server lag / settings race — keep optimistic pin in the map.
            continue
          }
          const cur = next[s.id]
          if (!cur || cur.provider !== s.llm_provider || cur.model !== s.model) {
            next[s.id] = { provider: s.llm_provider, model: s.model }
            changed = true
          }
        }
      }
      return changed ? next : prev
    })
  }, [sessions, activeId])

  // Restore per-session provider/model only when the *tab* changes — not on every
  // sessions poll / map hydrate (that fought global Settings save and flipped
  // Demo ↔ xAI continuously).
  const lastLlmTabRef = useRef<string>('')
  useEffect(() => {
    if (!activeId) return
    if (lastLlmTabRef.current === activeId) return
    lastLlmTabRef.current = activeId
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
    // sessionLlmMap/sessions read intentionally only on activeId change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

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
    const { tauriInvoke } = await import('./api/tauri')
    // Must finish writing desktop.json BEFORE quit_app kills the process —
    // fire-and-forget prefs save was why "Don't show again" never stuck.
    if (dontWarnAgain) {
      try {
        localStorage.setItem('remedy.skipQuitServerWarning', '1')
      } catch {
        /* */
      }
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
      }
    }
    try {
      // Race: if quit_app hangs, still try to leave
      await Promise.race([
        tauriInvoke('quit_app'),
        new Promise<void>((resolve) => window.setTimeout(resolve, 2500)),
      ])
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
      const { tauriInvoke } = await import('./api/tauri')
      // Fast path: localStorage or disk prefs
      try {
        if (localStorage.getItem('remedy.skipQuitServerWarning') === '1') {
          await tauriInvoke('quit_app')
          return
        }
      } catch {
        /* */
      }
      try {
        const prefs = await tauriInvoke<{ skip_quit_server_warning?: boolean }>(
          'get_desktop_prefs',
        )
        if (prefs?.skip_quit_server_warning) {
          try {
            localStorage.setItem('remedy.skipQuitServerWarning', '1')
          } catch {
            /* */
          }
          await tauriInvoke('quit_app')
          return
        }
      } catch {
        /* fall through to confirm path */
      }
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

  /** Refresh model list via GET /models[?provider=…] (live endpoint discovery).
   *  Stable deps (no `model`) so picking a model cannot re-fire boot load loops. */
  const refreshModels = useCallback(async (opts?: {
    selectDefault?: boolean
    /** Discover for this provider without requiring it to be active in config. */
    provider?: string
  }) => {
    try {
      const q = opts?.provider
        ? `?provider=${encodeURIComponent(opts.provider)}`
        : ''
      const data = await apiFetch<{
        models: ModelInfo[]
        default: string
        provider?: string
      }>(`/models${q}`)
      let list = data.models || []
      const activeProv = (
        data.provider || opts?.provider || llmProvider || ''
      ).toLowerCase()
      if (activeProv === 'demo') {
        const { demoModelOptions, isDemoModelAllowed, DEMO_DEFAULT_MODEL } =
          await import('./utils/demoModels')
        list = demoModelOptions(list).map((m) => ({
          id: m.id,
          name: m.name,
          provider: 'demo',
        }))
        setModels(list)
        if (opts?.selectDefault) {
          const def =
            list.find((m) => m.id === data.default && isDemoModelAllowed(m.id))
            ?? list.find((m) => m.id === DEMO_DEFAULT_MODEL)
            ?? list[0]
          if (def) setModel(def.id)
        }
        return { ...data, models: list, provider: 'demo' }
      }
      // Keep prior models for *other* providers so switching back stays warm;
      // replace entries for this provider with the live list.
      setModels((prev) => {
        const others = prev.filter((m) => m.provider && m.provider !== activeProv)
        const tagged = list.map((m) => ({
          ...m,
          provider: m.provider || activeProv,
        }))
        return [...tagged, ...others]
      })
      if (opts?.selectDefault) {
        const def = list.find((m) => m.id === data.default) ?? list[0]
        if (def) setModel(def.id)
      }
      return { ...data, models: list }
    } catch (e: unknown) {
      console.warn('Model refresh failed:', e instanceof Error ? e.message : e)
      return null
    }
  }, [llmProvider])

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
      // Wait for session list so activeId is set before the user can send.
      try {
        await sessionsPromise
      } catch {
        /* refresh already swallows */
      }
      if (cancelled) return

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
        const pn = (settings.name || '').trim()
        if (pn) setPartnerName(pn)
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
        // Prefer settings/provider picker model so first send is not empty-model.
        if (settings?.llm_model) {
          setModel(settings.llm_model)
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
    const s = await create()
    if (s) {
      setOpenTabs((prev) => new Set([...prev, s.id]))
    }
  }, [create])

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
        ).replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')

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
        let useModel = model
        if (!useModel?.trim()) {
          try {
            const s = await getSettings()
            if (s.llm_model) {
              useModel = s.llm_model
              setModel(s.llm_model)
            }
            if (s.llm_provider) setLlmProvider(s.llm_provider)
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
        // Sticky multi-tab bind: prefer this session's provider+model pair.
        const mapOv = sessionLlmMap[sid!]
        const useProvider =
          mapOv?.provider
          || sess?.llm_provider
          || llmProvider
          || undefined
        if (mapOv?.model) useModel = mapOv.model
        else if (sess?.model && sess.llm_provider) useModel = sess.model
        // While streaming, send() queues (after) or interrupts based on opts.mode.
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
      notify,
      setLlmProvider,
      runningCount,
      streaming,
      sessionLlmMap,
      llmProvider,
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

  // Notify only on streaming true→false edge for a turn we started (not history loads).
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    const was = wasStreamingRef.current
    wasStreamingRef.current = streaming
    if (was && !streaming && messages.length > 0) {
      const last = messages[messages.length - 1]
      if (last && last.role === 'assistant' && last.content) {
        notify('Remedy', {
          body: `Response ready — ${last.content.slice(0, 80)}...`,
          silent: false,
        })
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
      { id: 'settings', label: 'Settings Panel', description: 'Open settings in the right rail', category: 'panel', action: () => openSettingsInRail() },
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
    openSettingsInRail,
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
  }, [handleNewSession, openHelp, helpOpen, openSettingsInRail])

  useKeyboardShortcuts(globalShortcuts)

  // Native Browser embed HWND paints above React — hide while full-window overlays open.
  useEffect(() => {
    browserStackSet('help', helpOpen)
    browserStackSet('palette', paletteOpen)
    browserStackSet('about', aboutOpen)
    browserStackSet('quit-warn', quitWarnOpen)
    browserStackSet('time-travel', timeTravelOpen)
    browserStackSet('usage', usageOpen)
    browserStackSet('concurrent-turn', Boolean(concurrentConfirm))
    browserStackSet('ask-user-name', askUserName)
    return () => {
      browserStackSet('help', false)
      browserStackSet('palette', false)
      browserStackSet('about', false)
      browserStackSet('quit-warn', false)
      browserStackSet('time-travel', false)
      browserStackSet('usage', false)
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
          model={model}
          provider={llmProvider}
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
                    pinUserLlm(String(s.llm_provider), String(s.llm_model))
                    setLlmProvider(s.llm_provider)
                    setModel(s.llm_model)
                    if (activeId) {
                      setSessionLlmMap((prev) => ({
                        ...prev,
                        [activeId]: {
                          provider: String(s.llm_provider),
                          model: String(s.llm_model),
                        },
                      }))
                      void applySessionLlm(
                        activeId,
                        String(s.llm_provider),
                        String(s.llm_model),
                        true,
                      ).catch(() => {})
                    }
                  }
                  setUserName((s.user_name || '').trim())
                  setToolProcessMode(normalizeToolProcess(s.tool_process))
                  return refreshModels({
                    provider: s.llm_provider ? String(s.llm_provider) : undefined,
                  })
                })
                .catch(() => refreshModels())
            }}
          />
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
            gridTemplateRows: 'auto minmax(0, 1fr) auto',
            flex: '1 1 0%',
            minWidth: 240,
            minHeight: 0,
            height: '100%',
            alignSelf: 'stretch',
            overflow: 'hidden',
            position: 'relative',
            background: 'var(--bg-primary)',
          }}
        >
          {planMode && (
            <div
              className="absolute top-2 right-2 z-10 px-2 py-0.5 text-xs font-semibold rounded pointer-events-none"
              style={{ background: 'var(--accent)', color: '#fff', opacity: 0.9 }}
            >
              Plan Mode
            </div>
          )}

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
            />
          </div>

          <div
            className="chat-middle-composer flex flex-col"
            style={{ position: 'relative', zIndex: 5 }}
          >
            {!streaming
              && activeId
              && getStreamJob(activeId)?.status === 'running' && (
              <div
                className="mx-3 mb-2 rounded-lg border px-3 py-2 text-xs flex flex-wrap items-center gap-2"
                style={{
                  borderColor: 'var(--accent)',
                  background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                  color: 'var(--text-primary)',
                }}
                role="status"
              >
                <span className="flex-1 min-w-[12rem]">
                  Still working in the background — switch away anytime; Stop
                  ends this session&apos;s turn only.
                </span>
                <button
                  type="button"
                  className="px-2 py-1 rounded font-semibold"
                  style={{
                    background: 'var(--error)',
                    color: '#fff',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                  onClick={() => stop()}
                >
                  Stop
                </button>
              </div>
            )}
            {streaming && streamStalled && (
              <div
                className="mx-3 mb-2 rounded-lg border px-3 py-2 text-xs flex flex-wrap items-center gap-2"
                style={{
                  borderColor: 'var(--warning, #d97706)',
                  background: 'rgba(217, 119, 6, 0.1)',
                  color: 'var(--text-primary)',
                }}
                role="status"
              >
                <span className="flex-1 min-w-[12rem]">
                  Provider quiet for {stallSeconds}s (long think or stalled stream).
                  Stream may be stuck.
                </span>
                <button
                  type="button"
                  className="px-2 py-1 rounded font-semibold"
                  style={{
                    background: 'var(--error)',
                    color: '#fff',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                  onClick={() => stop()}
                >
                  Stop
                </button>
                <button
                  type="button"
                  className="px-2 py-1 rounded font-semibold"
                  style={{
                    background: 'var(--accent)',
                    color: '#fff',
                    border: 'none',
                    cursor: 'pointer',
                  }}
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
              model={model}
              provider={llmProvider}
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
              // Explicit Save Settings: become the pin source of truth.
              if (s.llm_provider && s.llm_model) {
                pinUserLlm(String(s.llm_provider), String(s.llm_model))
                setLlmProvider(s.llm_provider)
                setModel(s.llm_model)
                if (activeId) {
                  setSessionLlmMap((prev) => ({
                    ...prev,
                    [activeId]: {
                      provider: String(s.llm_provider),
                      model: String(s.llm_model),
                    },
                  }))
                  void applySessionLlm(
                    activeId,
                    String(s.llm_provider),
                    String(s.llm_model),
                    true,
                  ).catch(() => {})
                }
              }
              setUserName((s.user_name || '').trim())
              setToolProcessMode(normalizeToolProcess(s.tool_process))
              return refreshModels({
                provider: s.llm_provider ? String(s.llm_provider) : undefined,
              })
            })
            .catch(() => refreshModels())
        }}
      />

      <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={model}
          models={models}
          uiMode={uiMode}
          onUiModeChange={(mode) => {
            setUiMode(mode)
            saveUiMode(mode)
          }}
          provider={llmProvider}
          connectedProviders={connectedProviders}
          onProviderModelChange={(prov, mid) => {
            if (streaming) return
            // Optimistic UI — pin this pick; ignore server renames that thrash the bar.
            pinUserLlm(prov, mid)
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
                  // Persist session + global so boot/settings reload keep the choice.
                  const r = await applySessionLlm(activeId, prov, mid, true)
                  // Do not adopt server-normalized provider/model here — that caused
                  // Demo ↔ xAI flips when global config lagged the session.
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
                  // Live discover models for the *selected* provider endpoint
                  refreshModels({ provider: prov }),
                ]),
              )
              .then(([conn]) => {
                setConnectedProviders(
                  conn.picker?.length ? conn.picker : conn.connected || [],
                )
                // Re-assert *user* pick after async (not server active_*).
                setLlmProvider(prov)
                setModel(mid)
              })
              .catch(() => {})
          }}
          onModelChange={(id) => {
            pinUserLlm(llmProvider, id)
            setModel(id)
            if (activeId) {
              setSessionLlmMap((prev) => ({
                ...prev,
                [activeId]: {
                  provider: prev[activeId]?.provider || llmProvider,
                  model: id,
                },
              }))
              void applySessionLlm(activeId, llmProvider, id, true)
                .then((r) => {
                  // Keep the id the user chose — no server snap.
                  setModel(id)
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
              .then(() => setModel(id))
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
            draggable={false}
            style={{
              height: 36,
              width: 'auto',
              maxWidth: 220,
              objectFit: 'contain',
              marginBottom: 12,
              display: 'block',
            }}
          />
          <div className="text-sm font-semibold mb-1">About Remedy</div>
          <div className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            Your personal AI partner — knowledge, design, code, and get-it-done.
          </div>
          <div
            className="text-xs mb-3 leading-relaxed rounded-lg px-3 py-2"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
            }}
          >
            <div className="font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
              From the creator
            </div>
            My name is Ahmi, I hope you enjoy my Remedy.
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
                openSettingsInRail()
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
