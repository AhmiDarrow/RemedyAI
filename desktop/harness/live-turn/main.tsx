import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MessageFeed } from '../../src/components/MessageFeed'
import { I18nProvider } from '../../src/i18n'
import { applyStoredUiPrefs } from '../../src/utils/chatPrefs'
import { SCENARIO_END, stateAt } from './replay'
import '../../src/index.css'

applyStoredUiPrefs()

const params = new URLSearchParams(location.search)
const fixed = params.get('done') ? SCENARIO_END + 1 : params.get('t')
const mode = (params.get('mode') as 'off' | 'medium' | 'full') || 'off'
const height = Number(params.get('h')) || 900
const history = Number(params.get('history')) || 0
// Slow the live replay down for scroll checks (?speed=0.5 = half speed).
const speed = Number(params.get('speed')) || 1

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
  const s = stateAt(t, Date.now(), history)
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
