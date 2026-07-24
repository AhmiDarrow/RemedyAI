import { useState, useCallback, useEffect, useMemo } from 'react'
import { Sidebar } from './components/Sidebar'
import { ApprovalBanner } from './components/ApprovalBanner'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar, type ThinkingLevel, type ApprovalMode } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { SettingsPanel } from './components/SettingsPanel'
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
import { listAgents, listCommands, exportSession } from './api/messages'
import { apiFetch } from './api/client'
import { getSettings, updateSettings } from './api/settings'
import { isPlaceholderTitle, titleFromPrompt } from './utils/sessionTitle'
import { tauriListen } from './api/tauri'
import { normalizeToolProcess, type ToolProcessMode } from './utils/toolLabels'

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
}: {
  children: React.ReactNode
  version?: string
  updateAvailable?: boolean
  onMenuAction?: (action: AppMenuAction) => void
}) {
  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-primary)' }}>
      <TitleBar
        version={version}
        updateAvailable={updateAvailable}
        onMenuAction={onMenuAction}
      />
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
    </div>
  )
}

export default function App() {
  const {
    sessions,
    activeId,
    setActiveId,
    create,
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
    send,
    stop,
    runCommand,
    addCommandMessage,
    beginEdit,
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
  const [models, setModels] = useState<ModelInfo[]>([])
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('medium')
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
    updateAvailable,
  } = useUpdateChecker()
  const [showSetupWizard, setShowSetupWizard] = useState(false)
  const [showUpdateScreen, setShowUpdateScreen] = useState(false)
  const [userName, setUserName] = useState('')
  const [askUserName, setAskUserName] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [appVersion, setAppVersion] = useState('')

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
        case 'new_session':
          void (async () => {
            const s = await create()
            if (s?.id) setOpenTabs((prev) => new Set([...prev, s.id]))
          })()
          break
        case 'check_updates':
          void checkUpdates()
          break
        case 'install_update':
          if (desktopInfo?.update_available) setShowUpdateScreen(true)
          else void checkUpdates()
          break
        case 'about':
          setAboutOpen(true)
          break
        case 'quit':
          break
        default:
          break
      }
    },
    [checkUpdates, desktopInfo, create],
  )

  useEffect(() => {
    if (isTauri()) {
      const handleReady = () => setServerState('ready')
      const handleError = (e: any) => {
        setServerState('error')
        setServerError(typeof e.payload === 'string' ? e.payload : 'Server failed to start')
      }
      ;(window as any).__TAURI_INTERNALS__?.invoke('plugin:event|listen', {
        event: 'server-ready', handler: handleReady,
      }).catch((e: any) => console.warn('Tauri listen(server-ready) failed:', e))
      ;(window as any).__TAURI_INTERNALS__?.invoke('plugin:event|listen', {
        event: 'server-error', handler: handleError,
      }).catch((e: any) => console.warn('Tauri listen(server-error) failed:', e))
    }
  }, [])

  // Tray menu → themed in-app panels
  useEffect(() => {
    if (!isTauri()) return
    let off: Array<() => void> = []
    void (async () => {
      off.push(await tauriListen('tray-open-settings', () => setPanel('settings')))
      off.push(await tauriListen('tray-check-updates', () => {
        void checkUpdates()
      }))
      off.push(await tauriListen('tray-about', () => setAboutOpen(true)))
    })()
    return () => {
      for (const u of off) u()
    }
  }, [checkUpdates])

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
    // Single settings fetch at startup (wizard gate + model selection + agents).
    Promise.all([
      refreshModels(),
      listAgents(),
      listCommands(),
      getSettings().catch(() => null),
    ]).then(([modelsData, agents, _commandsData, settings]) => {
        setAgentDefs(Array.isArray(agents) ? agents : agents?.agents || [])
        if (settings) {
          // First-run gate: block chat UI until setup completes or is skipped.
          // Skip / finish both set setup_completed so this does not reappear.
          if (settings.needs_setup || !settings.setup_completed) {
            setShowSetupWizard(true)
          }
          if (settings.llm_model) {
            setModel(settings.llm_model)
          } else if (modelsData?.default) {
            setModel(modelsData.default)
          }
          const tl = String(settings.thinking_level || 'medium').toLowerCase()
          if (tl === 'off' || tl === 'low' || tl === 'medium' || tl === 'high') {
            setThinkingLevel(tl)
          }
          const am = String(settings.approval_mode || 'ask').toLowerCase()
          if (am === 'ask' || am === 'auto') setApprovalMode(am)
          setToolProcessMode(normalizeToolProcess(settings.tool_process ?? settings.show_tool_calls))
          const un = (settings.user_name || '').trim()
          setUserName(un)
          if (settings.version) setAppVersion(String(settings.version))
          // Ask for name after setup when missing (skip while wizard is open).
          const needsWizard = settings.needs_setup || !settings.setup_completed
          if (!needsWizard && !un) {
            try {
              const skipped = localStorage.getItem('remedy.userName.skipped')
              if (!skipped) setAskUserName(true)
            } catch {
              setAskUserName(true)
            }
          }
        } else if (modelsData?.default) {
          setModel(modelsData.default)
        }
      })
      .catch((e: any) => {
        setServerState('error')
        setServerError(`Failed to load server config: ${e?.message || e}`)
      })
  }, [serverState, refreshModels])

  const handleNewSession = useCallback(async () => {
    const s = await create()
    if (s) {
      setOpenTabs((prev) => new Set([...prev, s.id]))
    }
  }, [create])

  useEffect(() => {
    if (serverState === 'ready') {
      refreshSessions()
    }
  }, [serverState, refreshSessions])

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

  const handleCommand = useCallback(
    async (command: string) => {
      const sid = activeId || (await create())?.id
      if (!sid) return { text: 'No session available.' }
      const result = await runCommand(command, sid)
      if (result.text && sid) {
        addCommandMessage(command, result.text)
      }
      if (result.action === 'new_session') {
        await handleNewSession()
      }
      return result
    },
    [runCommand, handleNewSession, activeId, create, addCommandMessage],
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
        send(text, model, sid, attachments)
        // Pull titles/message counts after the turn starts (server may have renamed).
        window.setTimeout(() => {
          void refreshSessions()
        }, 1200)
      }
    },
    [send, model, handleCommand, activeId, create, sessions, rename, refreshSessions],
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

  const handleExport = useCallback(
    async (sessionId: string) => {
      try {
        const { markdown, filename } = await exportSession(sessionId)
        const blob = new Blob([markdown], { type: 'text/markdown' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        a.click()
        URL.revokeObjectURL(url)
      } catch (e: unknown) {
        console.warn('Export failed:', e instanceof Error ? e.message : e)
      }
    },
    [],
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
      { id: 'palette', label: 'Command Palette', description: 'Open this palette', category: 'general', action: () => setPaletteOpen(true) },
      { id: 'plan', label: 'Toggle Plan Mode', description: 'Switch between plan and build', category: 'general', action: () => setPlanMode((p) => !p) },
      { id: 'memory', label: 'Memory Panel', description: 'Toggle memory panel', category: 'panel', action: () => setPanel((p) => (p === 'memory' ? null : 'memory')) },
      { id: 'skills', label: 'Skills Panel', description: 'Toggle skills panel', category: 'panel', action: () => setPanel((p) => (p === 'skills' ? null : 'skills')) },
      { id: 'settings', label: 'Settings Panel', description: 'Toggle settings panel', category: 'panel', action: () => setPanel((p) => (p === 'settings' ? null : 'settings')) },
      {
        id: 'help',
        label: 'Keyboard Shortcuts',
        description: 'Show help and hotkeys (Ctrl+/)',
        category: 'general',
        action: () => {
          setPanel('settings')
          // Help section is in settings; also inject /help into chat if possible
          void handleCommand('/help')
        },
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
  }, [sessions, agentDefs, models, handleNewSession, handleSelect, handleCommand])

  useKeyboardShortcuts([
    { key: 'n', ctrl: true, handler: handleNewSession },
    { key: 'p', ctrl: true, handler: () => setPaletteOpen((o) => !o) },
    { key: 'k', ctrl: true, handler: () => setPaletteOpen((o) => !o) },
    { key: 'b', ctrl: true, handler: () => setPlanMode((p) => !p) },
    { key: ',', ctrl: true, handler: () => setPanel((p) => (p === 'settings' ? null : 'settings')) },
    {
      key: '/',
      ctrl: true,
      allowInInput: true,
      handler: () => {
        void handleCommand('/help')
      },
    },
    {
      key: 'F1',
      ctrl: false,
      allowInInput: true,
      handler: () => {
        void handleCommand('/help')
      },
    },
    {
      key: 'Escape',
      ctrl: false,
      allowInInput: true,
      handler: () => {
        setPaletteOpen(false)
        setPanel(null)
      },
    },
  ])

  const shellProps = {
    version: appVersion || updateInfo?.current_version || desktopInfo?.current_version,
    updateAvailable,
    onMenuAction: handleMenuAction,
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

  if (serverState === 'error') {
    return (
      <AppShell {...shellProps}>
        <div className="flex items-center justify-center h-full flex-col gap-4" style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
          <div style={{ color: 'var(--error)' }} className="text-lg font-medium">
            {serverError || 'Server connection failed'}
          </div>
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
            The Remedy server could not start. Try restarting the app.
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => {
                setServerError('')
                // Always re-enter splash so min duration + health poll apply.
                setServerState('connecting')
                if (isTauri()) {
                  const invoke = (window as any).__TAURI_INTERNALS__?.invoke
                  if (invoke) {
                    invoke('restart_server').catch((e: unknown) => {
                      const msg = e instanceof Error ? e.message : String(e)
                      setServerState('error')
                      setServerError(msg || 'Failed to restart server')
                    })
                  }
                }
              }}
              className="px-5 py-2 rounded-md text-sm"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              Retry
            </button>
            <button
              onClick={() => {
                if (!isTauri()) return
                const invoke = (window as any).__TAURI_INTERNALS__?.invoke
                if (!invoke) {
                  setServerError((prev) => prev || 'Cannot open data folder (Tauri bridge unavailable)')
                  return
                }
                invoke('open_data_folder').catch((e: unknown) => {
                  const msg = e instanceof Error ? e.message : String(e)
                  console.warn('Open data folder failed:', msg)
                  setServerError((prev) => `${prev ? prev + ' — ' : ''}Could not open data folder: ${msg}`)
                })
              }}
              className="px-5 py-2 rounded-md text-sm"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
            >
              Open Data Folder
            </button>
          </div>
        </div>
      </AppShell>
    )
  }

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
        onDelete={(id) => {
          remove(id)
          handleCloseTab(id)
        }}
        onRename={(id, title) => {
          void rename(id, title)
        }}
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
              disabled={streaming}
              planMode={planMode}
              agents={agentDefs}
              editDraft={editDraft}
              sessionId={activeId}
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

          <MemoryPanel
            open={panel === 'memory'}
            onClose={() => setPanel(null)}
          />
          <SkillsPanel
            open={panel === 'skills'}
            onClose={() => setPanel(null)}
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
            onCheckUpdates={() => {
              void checkUpdates()
            }}
            onInstallUpdate={() => {
              if (desktopInfo?.update_available) setShowUpdateScreen(true)
              else void checkUpdates()
            }}
            models={models}
            toolProcessMode={toolProcessMode}
            onToolProcessChange={(mode) => {
              setToolProcessMode(mode)
              updateSettings({ tool_process: mode }).catch(() => {})
            }}
            onSettingsSaved={() => {
              void getSettings()
                .then((s) => {
                  if (s.llm_model) setModel(s.llm_model)
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
          onModelChange={(id) => {
            setModel(id)
            // Persist + hot-apply on server so it survives restarts and chat uses it now.
            updateSettings({ llm_model: id })
              .then((r) => {
                if (r.llm_model) setModel(r.llm_model)
              })
              .catch(() => {})
          }}
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
          updateAvailable={updateAvailable}
          onCheckUpdates={checkUpdates}
          onInstallUpdate={() => {
            if (desktopInfo?.update_available) setShowUpdateScreen(true)
            else void checkUpdates()
          }}
        />
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
