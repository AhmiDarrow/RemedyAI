import { useState, useCallback } from 'react'
import { Sidebar } from './components/Sidebar'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { MemoryPanel, SkillsPanel } from './components/Panels'
import { useSessions } from './hooks/useSessions'
import { useMessages } from './hooks/useMessages'
import { useTheme } from './hooks/useTheme'
import { revertMessageApi } from './api/messages'

const DEFAULT_MODEL = 'gpt-4o-mini'

export default function App() {
  const { sessions, activeId, setActiveId, create, remove } = useSessions()
  const { messages, loading: messagesLoading, streaming, partialText, send, stop, runCommand, load } = useMessages(activeId)
  const { themeId, theme, set: setTheme } = useTheme()
  const [model] = useState(DEFAULT_MODEL)
  const [planMode, setPlanMode] = useState(false)
  const [panel, setPanel] = useState<'memory' | 'skills' | null>(null)
  const [openTabs, setOpenTabs] = useState<Set<string>>(new Set())

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

  const handleSend = useCallback(
    (text: string) => {
      if (text.startsWith('/')) {
        handleCommand(text)
      } else {
        send(text, model)
      }
    },
    [send, model],
  )

  const handleCommand = useCallback(
    async (command: string) => {
      const result = await runCommand(command)
      if (result.action === 'new_session') {
        await handleNewSession()
      }
      return result
    },
    [runCommand, handleNewSession],
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

  return (
    <div className="flex h-full" style={{ background: 'var(--bg-primary)' }}>
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

      <div className="flex-1 flex flex-col min-w-0">
        <TabBar
          tabs={sessions.filter((s) => openTabs.has(s.id))}
          activeId={activeId}
          onSelect={handleSelect}
          onClose={handleCloseTab}
          onNew={handleNewSession}
        />

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
        </div>

        <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={model}
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
