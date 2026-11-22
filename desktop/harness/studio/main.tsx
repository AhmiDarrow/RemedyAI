/**
 * Dev-only visual harness for the current Studio chrome.
 *   npx vite --config harness/vite.config.ts --port 5201 --strictPort
 *   http://127.0.0.1:5201/harness/studio/?theme=forest&screen=chat
 *
 * screen=chat (default) — sessions sidebar, a finished turn, composer, status bar
 * screen=empty — selected empty session, no messages
 */
import { StrictMode, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ChatSessionHeader } from '../../src/components/ChatSessionHeader'
import { Composer } from '../../src/components/Composer'
import { MessageFeed } from '../../src/components/MessageFeed'
import { Sidebar } from '../../src/components/Sidebar'
import { StatusBar } from '../../src/components/StatusBar'
import { I18nProvider } from '../../src/i18n'
import { applyTheme, getResolvedTheme, type ThemeId, THEMES } from '../../src/themes'
import { applyFonts, DEFAULT_FONTS } from '../../src/utils/fontPrefs'
import type { ChatSession } from '../../src/types'
import { SCENARIO_END, stateAt } from '../live-turn/replay'
import '../../src/index.css'

const params = new URLSearchParams(location.search)
const themeParam = (params.get('theme') || 'forest') as ThemeId
const screen = params.get('screen') || 'chat'
const themeId: ThemeId = themeParam in THEMES || themeParam === 'system' ? themeParam : 'forest'

applyFonts(DEFAULT_FONTS)
applyTheme(getResolvedTheme(themeId))
document.documentElement.setAttribute('data-density', 'cozy')
document.documentElement.classList.add('app-ready')
document.body.style.background = 'var(--bg-primary)'

const NOW = '2026-11-22T21:00:00.000Z'
const SESSIONS: ChatSession[] = [
  {
    id: 's1',
    title: 'Port to RemedyUI',
    model: 'remedy',
    agent: null,
    message_count: 6,
    created_at: '2026-11-21T18:12:00.000Z',
    updated_at: '2026-11-22T20:48:00.000Z',
  },
  {
    id: 's2',
    title: 'House rules',
    model: 'remedy',
    agent: null,
    message_count: 2,
    created_at: '2026-11-20T11:00:00.000Z',
    updated_at: '2026-11-22T09:15:00.000Z',
  },
  {
    id: 's3',
    title: 'New chat',
    model: null,
    agent: null,
    message_count: 0,
    created_at: NOW,
    updated_at: NOW,
  },
]

function Harness() {
  const empty = screen === 'empty'
  const [activeId, setActiveId] = useState(empty ? 's3' : 's1')
  const [theme, setTheme] = useState<ThemeId>(themeId)
  const resolved = useMemo(() => getResolvedTheme(theme), [theme])
  const feed = empty ? { messages: [], partialText: '', textBreaks: [] as number[], processSteps: [], activeTools: [], taskProgress: null, streaming: false } : stateAt(SCENARIO_END + 1, Date.now(), 0)
  const active = SESSIONS.find((s) => s.id === activeId) ?? SESSIONS[0]

  return (
    <div
      className="flex flex-col h-full min-h-0"
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
      data-harness-ready="1"
    >
      <div className="studio-shell flex flex-1 min-h-0 h-full items-stretch">
        <Sidebar
          sessions={SESSIONS}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={() => setActiveId('s3')}
          onDelete={() => {}}
        />
        <div className="chat-middle flex flex-col flex-1 min-h-0 min-w-0">
          <ChatSessionHeader
            title={active.title}
            partnerName="Remedy"
            modelLabel={active.model}
            providerLabel="local"
            messageCount={empty ? 0 : feed.messages.length}
          />
          <div className="chat-middle-feed flex-1 min-h-0">
            <MessageFeed
              messages={feed.messages}
              partialText={empty ? '' : feed.partialText}
              textBreaks={empty ? [] : feed.textBreaks}
              streaming={false}
              loading={false}
              processSteps={empty ? [] : feed.processSteps}
              activeTools={empty ? [] : feed.activeTools}
              taskProgress={empty ? [] : feed.taskProgress}
              toolProcessMode="off"
              userName="Joe"
              partnerName="Remedy"
              sessionId={activeId}
              stickNonce={0}
            />
          </div>
          <div className="chat-middle-composer flex flex-col">
            <Composer
              onSend={() => {}}
              onStop={() => {}}
              onCommand={() => {}}
              streaming={false}
              disabled={false}
              slashCommands={[
                { name: '/help', description: 'List commands', aliases: [], arguments: null },
                { name: '/new', description: 'New session', aliases: [], arguments: null },
              ]}
              sessionId={activeId}
              acceptNativeDrops={false}
            />
          </div>
        </div>
      </div>
      <StatusBar
        sessionId={activeId}
        streaming={false}
        model="remedy"
        models={[{ id: 'remedy', name: 'Remedy', provider: 'local', default: true }]}
        provider="local"
        connectedProviders={[{
          id: 'local',
          name: 'Local',
          base_url: 'http://127.0.0.1:7400',
          models: [],
          default_model: 'remedy',
          auth: [],
          oauth: false,
          env_keys: [],
          show_base_url: false,
          advanced: false,
          connected: true,
          connect_reason: 'ok',
          enabled: true,
          picker_eligible: true,
          last_model: 'remedy',
        }]}
        thinkingLevel="medium"
        approvalMode="ask"
        themeId={theme}
        theme={resolved}
        onThemeChange={(id) => {
          setTheme(id)
          applyTheme(getResolvedTheme(id))
        }}
        planMode={false}
        onTogglePlanMode={() => {}}
        onTogglePanel={() => {}}
        updateAvailable={false}
        onCheckUpdates={() => {}}
        surface="studio"
      />
    </div>
  )
}

document.fonts.ready.then(() => {
  document.documentElement.dataset.fontsReady = '1'
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <Harness />
    </I18nProvider>
  </StrictMode>,
)
