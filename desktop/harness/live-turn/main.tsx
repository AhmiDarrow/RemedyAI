import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MessageFeed } from '../../src/components/MessageFeed'
import { I18nProvider } from '../../src/i18n'
import { applyStoredUiPrefs } from '../../src/utils/chatPrefs'
import { SCENARIO_END, stateAt } from './replay'
import '../../src/index.css'
import type { MessageReactions } from '../../src/types'
import { withReaction } from '../../src/utils/reactions'

applyStoredUiPrefs()

const params = new URLSearchParams(location.search)
const fixed = params.get('done') ? SCENARIO_END + 1 : params.get('t')
const mode = (params.get('mode') as 'off' | 'medium' | 'full') || 'off'
const height = Number(params.get('h')) || 900
const history = Number(params.get('history')) || 0
// Slow the live replay down for scroll checks (?speed=0.5 = half speed).
const speed = Number(params.get('speed')) || 1
// ?reactions=1 seeds Remedy's 👍 on the last user message and makes the
// owner's picker live (state is local to the harness).
const reactionsOn = Boolean(params.get('reactions'))
const seedUser = params.get('mine') || ''
const todosOn = Boolean(params.get('todos'))
const DEMO_TODOS = [
  { id: '1', content: 'Explore the addon folder', status: 'completed' as const },
  { id: '2', content: 'Patch the loader entry', status: 'in_progress' as const },
  { id: '3', content: 'Run the addon self-check', status: 'pending' as const },
]

function Harness() {
  const [t, setT] = useState(() => (fixed != null ? Number(fixed) : 0))
  useEffect(() => {
    if (fixed != null) return
    const start = performance.now()
    const id = setInterval(() => {
      const next = (performance.now() - start) * speed
      setT(next)
      if (next > SCENARIO_END + 500) clearInterval(id)
    }, 50)
    return () => clearInterval(id)
  }, [])
  const base = stateAt(t, Date.now(), history)
  const [reacts, setReacts] = useState<Record<string, MessageReactions>>({})
  let messages = base.messages.map((m) => (reacts[m.id] ? { ...m, reactions: reacts[m.id] } : m))
  if (reactionsOn) {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user')
    if (lastUser && !reacts[lastUser.id]) messages = withReaction(messages, lastUser.id, 'agent', '👍')
    const lastReply = [...messages].reverse().find((m) => m.role === 'assistant')
    if (seedUser && lastReply && !reacts[lastReply.id]) messages = withReaction(messages, lastReply.id, 'user', seedUser)
  }
  const s = { ...base, messages }
  const onReact = (id: string, emoji: string) => {
    const next = withReaction(messages, id, 'user', emoji).find((m) => m.id === id)
    setReacts((r) => ({ ...r, [id]: next?.reactions || {} }))
  }
  return (
    <div className="harness-shell" style={{ width: 760, height, position: 'relative', margin: '0 auto' }}>
      <MessageFeed
        messages={s.messages}
        partialText={s.partialText}
        textBreaks={s.textBreaks}
        partialThinking={s.partialThinking}
        streaming={s.streaming}
        loading={false}
        processSteps={s.processSteps}
        activeTools={s.activeTools}
        taskProgress={s.taskProgress}
        toolProcessMode={mode}
        userName="Sam"
        partnerName="Remedy"
        sessionId="demo"
        stickNonce={0}
        buildTodos={todosOn ? DEMO_TODOS : []}
        onReact={reactionsOn ? onReact : undefined}
      />
      <textarea data-testid="composer" aria-label="Composer" style={{ position: 'absolute', bottom: -60, left: 0, width: 300, height: 40 }} />
    </div>
  )
}

document.body.style.background = 'var(--bg-primary)'
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <Harness />
    </I18nProvider>
  </StrictMode>,
)
