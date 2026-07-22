import { useState, useCallback, useEffect, useMemo } from 'react'
import { Sidebar } from './components/Sidebar'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { SettingsPanel } from './components/SettingsPanel'
import { SplashScreen } from './components/SplashScreen'
import { CommandPalette, type CommandItem } from './components/CommandPalette'
import { useSessions } from './hooks/useSessions'
import { useMessages } from './hooks/useMessages'
import { useTheme } from './hooks/useTheme'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { useNotifications } from './hooks/useNotifications'
import { revertMessageApi, listAgents, listCommands, exportSession } from './api/messages'
import { apiFetch } from './api/client'

export interface ModelInfo {
  id: string
  name: string
  provider: string
  default: boolean
}

type ServerState = 'connecting' | 'ready' | 'error'

function isTauri(): boolean {
  return typeof window !== 'undefined' && (window as any).__TAURI__ !== undefined
}

export default function App() {
  const { sessions, activeId, setActiveId, create, remove, refresh: refreshSessions } = useSessions()
  const { messages, loading: messagesLoading, streaming, partialText, send, stop, runCommand, load } = useMessages(activeId)
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

  useEffect(() => {
    if (isTauri()) {
      const handleReady = () => setServerState('ready')
      const handleError = (e: any) => {
        setServerState('error')
        setServerError(typeof e.payload === 'string' ? e.payload : 'Server failed to start')
      }
      ;(window as any).__TAURI_INTERNALS__?.invoke('plugin:event|listen', {
        event: 'server-ready', handler: handleReady,
      }).catch(() => {})
      ;(window as any).__TAURI_INTERNALS__?.invoke('plugin:event|listen', {
        event: 'server-error', handler: handleError,
      }).catch(() => {})
    }
  }, [])

  useEffect(() => {
    if (serverState !== 'ready') return
    Promise.all([
      apiFetch<{ models: ModelInfo[]; default: string }>('/models'),
      listAgents(),
      listCommands(),
    ]).then(([data, agents, _commandsData]) => {
        setModels(data.models)
        const def = data.models.find((m) => m.id === data.default) ?? data.models[0]
        if (def) setModel(def.id)
        setAgentDefs(Array.isArray(agents) ? agents : agents?.agents || [])
      })
      .catch(() => {})
  }, [serverState])

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
      if (result.action === 'new_session') {
        await handleNewSession()
      }
      return result
    },
    [runCommand, handleNewSession, activeId, create],
  )

  const handleSend = useCallback(
    async (text: string) => {
      if (text.startsWith('/')) {
        handleCommand(text)
      } else {
        const sid = activeId || (await create())?.id
        if (sid) send(text, model, sid)
      }
    },
    [send, model, handleCommand, activeId, create],
  )

  const handleRevert = useCallback(
    async (msgId: string) => {
      if (!activeId) return
      try {
        await revertMessageApi(activeId, msgId)
        await load()
      } catch {
        // ignore
      }
    },
    [activeId, load],
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
      } catch {
        // ignore
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
  }, [sessions, agentDefs, models, handleNewSession, handleSelect])

  useKeyboardShortcuts([
    { key: 'n', handler: handleNewSession },
    { key: 'p', handler: () => setPaletteOpen((o) => !o) },
    { key: 'b', handler: () => setPlanMode((p) => !p) },
    { key: 'Escape', ctrl: false, handler: () => { setPaletteOpen(false); setPanel(null) } },
  ])

  if (serverState === 'connecting') {
    return (
      <SplashScreen
        onReady={() => setServerState('ready')}
        onError={(msg) => { setServerState('error'); setServerError(msg) }}
      />
    )
  }

  if (serverState === 'error') {
    return (
      <div className="flex items-center justify-center h-full flex-col gap-4" style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
        <div style={{ color: 'var(--error)' }} className="text-lg font-medium">
          {serverError || 'Server connection failed'}
        </div>
        <button
          onClick={() => setServerState('connecting')}
          className="px-4 py-2 rounded-md text-sm"
          style={{ background: 'var(--accent)', color: '#fff' }}
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full" style={{ background: 'var(--bg-primary)' }}>
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

      <div className="flex-1 flex flex-col min-w-0 relative">
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
          <div className="flex-1 flex flex-col min-w-0">
            <MessageFeed
              messages={messages}
              partialText={partialText}
              streaming={streaming}
              loading={messagesLoading}
              planMode={planMode}
              onRevert={handleRevert}
            />

            <Composer
              onSend={handleSend}
              onStop={stop}
              onCommand={handleCommand}
              streaming={streaming}
              disabled={!activeId}
              planMode={planMode}
              agents={agentDefs}
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
          />
        </div>

        <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={model}
          models={models}
          onModelChange={setModel}
          themeId={themeId}
          theme={theme}
          onThemeChange={setTheme}
          planMode={planMode}
          onTogglePlanMode={() => setPlanMode((p) => !p)}
          panel={panel}
          onTogglePanel={(p) => setPanel((prev) => (prev === p ? null : p))}
        />
      </div>
    </div>
  )
}
