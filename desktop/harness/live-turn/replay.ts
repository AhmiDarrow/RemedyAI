import type { ChatMessage } from '../../src/types'
import type { TaskProgressInfo } from '../../src/components/TaskProgress'
import {
  applyLiveToolCall,
  applyLiveToolResult,
  type LiveToolChip,
  type ProcessStep,
} from '../../src/utils/toolLabels'
import { ANSWER, buildScenario } from './scenario'
import { narrationAtBreak } from '../../src/utils/liveSegments'

export interface FeedState {
  messages: ChatMessage[]
  streaming: boolean
  partialText: string
  textBreaks: number[]
  partialThinking: string
  processSteps: ProcessStep[]
  activeTools: LiveToolChip[]
  taskProgress: TaskProgressInfo | null
}

const USER: ChatMessage = {
  id: 'u1',
  role: 'user',
  content: 'The portal addon stopped loading after the last update. Can you fix it?',
  thinking: null,
  tool_calls: [],
  tool_results: [],
  model: null,
  agent: null,
  tokens: null,
  created_at: new Date(2026, 10, 14, 15, 0).toISOString(),
  reverted: false,
}

/**
 * Replays the scenario up to `t` ms the way the desktop stream handler does:
 * text deltas append (the runtime opens each new model round with a blank
 * line), a tool call marks a text break, results resolve steps.
 */
function historyPairs(n: number): ChatMessage[] {
  const out: ChatMessage[] = []
  for (let i = 0; i < n; i++) {
    const at = new Date(2026, 10, 14, 14, i).toISOString()
    const base = { thinking: null, tool_calls: [], tool_results: [], model: null, agent: null, tokens: null, created_at: at, reverted: false }
    out.push({ ...base, id: `hu${i}`, role: 'user', content: `Earlier question ${i + 1}: can you check the addon list?` })
    out.push({
      ...base,
      id: `ha${i}`,
      role: 'assistant',
      content: `Earlier answer ${i + 1}. The addon list has six entries and all of them were enabled at the time. Nothing else looked out of place in the folder.`,
    })
  }
  return out
}

export function stateAt(t: number, wallNow = Date.now(), history = 0): FeedState {
  const earlier = historyPairs(history)
  const { events, end } = buildScenario()
  const done = t >= end
  const base = wallNow - Math.min(t, end)
  let text = ''
  let thinking = ''
  const breaks: number[] = []
  let steps: ProcessStep[] = []
  let tools: LiveToolChip[] = []
  let progress: TaskProgressInfo | null = null
  let lastWasTool = false
  const calls: Array<{ name: string; args: Record<string, unknown>; id: string; narration?: string }> = []
  const results: Array<{ name: string; output: string; error?: string; id: string }> = []
  for (const e of events) {
    if (e.at > t) break
    const now = base + e.at
    switch (e.type) {
      case 'thinking':
        thinking += e.text
        break
      case 'text':
        if (lastWasTool && text) text += '\n\n'
        lastWasTool = false
        text += e.text
        break
      case 'tool_call': {
        // Same cut as markJobTextBreak + useMessages: narration rides on the step.
        const before = [...breaks]
        const last = breaks.length ? breaks[breaks.length - 1]! : 0
        if (text.length > last && text.slice(last).trim()) breaks.push(text.length)
        const narration = narrationAtBreak(text, before, breaks)
        ;({ steps, tools } = applyLiveToolCall(
          steps,
          tools,
          { name: e.name, args: e.args, callId: e.id, narration },
          now,
        ))
        // Same record the runtime saves (stream.go adds narration per call).
        calls.push({ name: e.name, args: e.args, id: e.id, ...(narration ? { narration } : {}) })
        lastWasTool = true
        break
      }
      case 'tool_result':
        ;({ steps, tools } = applyLiveToolResult(
          steps,
          tools,
          { name: e.name, preview: e.preview, ok: e.ok, callId: e.id },
          now,
        ))
        results.push(
          e.ok
            ? { name: e.name, output: e.preview, id: e.id }
            : { name: e.name, output: '', error: e.preview, id: e.id },
        )
        break
      case 'progress':
        progress = { label: e.label, step: e.step, total: e.total, percent: (100 * e.step) / e.total }
        break
      case 'status':
        break
    }
  }
  if (done) {
    const saved: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: text.trim(),
      thinking: thinking.trim() || null,
      tool_calls: calls,
      tool_results: results,
      model: 'demo-model',
      agent: null,
      tokens: null,
      created_at: new Date(2026, 10, 14, 15, 2).toISOString(),
      reverted: false,
      request_id: 'req-demo',
    }
    return {
      messages: [...earlier, USER, saved],
      streaming: false,
      partialText: '',
      textBreaks: [],
      partialThinking: '',
      processSteps: [],
      activeTools: [],
      taskProgress: null,
    }
  }
  return {
    messages: [...earlier, USER],
    streaming: true,
    partialText: text,
    textBreaks: breaks,
    partialThinking: thinking,
    processSteps: steps,
    activeTools: tools,
    taskProgress: progress,
  }
}

export const SCENARIO_END = buildScenario().end
export const ANSWER_TEXT = ANSWER
