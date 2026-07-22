import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { MessageFeed } from './components/MessageFeed'
import { Composer } from './components/Composer'
import { StatusBar } from './components/StatusBar'
import { useSessions } from './hooks/useSessions'
import { useMessages } from './hooks/useMessages'
import { useTheme } from './hooks/useTheme'

const DEFAULT_MODEL = 'gpt-4o-mini'

export default function App() {
  const { sessions, activeId, setActiveId, create, remove } = useSessions()
  const { messages, loading: messagesLoading, streaming, partialText, send, stop, runCommand } = useMessages(activeId)
  const { themeId, theme, set: setTheme } = useTheme()
  const [model] = useState(DEFAULT_MODEL)

  const handleNewSession = async () => {
    await create()
  }

  const handleSend = (text: string) => {
    if (text.startsWith('/')) {
      runCommand?.(text)
    } else {
      send(text, model)
    }
  }

  const handleCommand = (command: string) => {
    runCommand?.(command)
  }

  return (
    <div className="flex h-full" style={{ background: 'var(--bg-primary)' }}>
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={handleNewSession}
        onDelete={remove}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <MessageFeed
          messages={messages}
          partialText={partialText}
          streaming={streaming}
          loading={messagesLoading}
        />

        <Composer
          onSend={handleSend}
          onStop={stop}
          onCommand={handleCommand}
          streaming={streaming}
          disabled={!activeId}
        />

        <StatusBar
          sessionId={activeId}
          streaming={streaming}
          model={model}
          themeId={themeId}
          theme={theme}
          onThemeChange={setTheme}
        />
      </div>
    </div>
  )
}
