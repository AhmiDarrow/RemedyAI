import { useState, useCallback, useEffect, useMemo } from 'react'
import { Sidebar } from './components/Sidebar'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { SettingsPanel } from './components/SettingsPanel'
import { SplashScreen } from './components/SplashScreen'
import { SetupWizard } from './components/SetupWizard'
import { UpdateScreen } from './components/UpdateScreen'
import { TitleBar } from './components/TitleBar'
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
function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-primary)' }}>
      <TitleBar />
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
    </div>
  )
}

export default function App() {
  const { sessions, activeId, setActiveId, create, remove, refresh: refreshSessions } = useSessions()
  const {
    messages,
    loading: messagesLoading,
    streaming,
    partialText,
    activeTools,
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
  const { themeId, theme, set: setTheme } = useTheme()
  const [model, setModel] = useState('gpt-4o-mini')
  const [models, setModels] = useState<ModelInfo[]>([])
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
        if (sid) send(text, model, sid, attachments)
      }
    },
    [send, model, handleCommand, activeId, create],
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

  if (serverState === 'connecting') {
    return (
      <AppShell>
        <SplashScreen
          onReady={() => setServerState('ready')}
          onError={(msg) => { setServerState('error'); setServerError(msg) }}
        />
      </AppShell>
    )
  }

  if (serverState === 'error') {
    return (
      <AppShell>
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
      <AppShell>
        <SetupWizard
          open={showSetupWizard}
          onComplete={() => {
            setShowSetupWizard(false)
            void getSettings()
              .then((s) => {
                if (s.llm_model) setModel(s.llm_model)
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
      <AppShell>
        <UpdateScreen
          info={desktopInfo}
          autoStart
          onClose={() => setShowUpdateScreen(false)}
        />
      </AppShell>
    )
  }

  return (
    <AppShell>
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
            <MessageFeed
              messages={messages}
              partialText={partialText}
              streaming={streaming}
              loading={messagesLoading}
              planMode={planMode}
              activeTools={activeTools}
              onEditUserMessage={handleEditUserMessage}
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
            onSettingsSaved={() => {
              void getSettings()
                .then((s) => {
                  if (s.llm_model) setModel(s.llm_model)
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
    </AppShell>
  )
}
