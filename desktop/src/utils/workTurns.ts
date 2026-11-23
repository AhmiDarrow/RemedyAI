import type { ChatMessage } from '../types'
import type { BuildTodo } from '../components/BuildTodos'
import { todosHaveOpen } from '../components/BuildTodos'
import { stepsFromMessageTools, type ProcessStep } from './toolLabels'

/** One coding/actions entry, anchored to the user message that started the turn. */
export type WorkTurn = {
  id: string
  userMessageId: string | null
  prompt: string
  createdAt: string
  assistantIds: string[]
  thinking: string
  steps: ProcessStep[]
  todos?: BuildTodo[]
  live?: boolean
}

/** Map each assistant (or user) message to the user turn that started it. */
export function turnIdForMessages(messages: ChatMessage[]): Map<string, string> {
  const map = new Map<string, string>()
  let current: string | null = null
  for (const m of messages) {
    if (m.reverted) continue
    if (m.role === 'user') {
      current = m.id
      map.set(m.id, m.id)
    } else if (current) {
      map.set(m.id, current)
    }
  }
  return map
}

export function promptExcerpt(text: string, max = 88): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  if (compact.length <= max) return compact
  return compact.slice(0, Math.max(0, max - 1)).trimEnd() + '…'
}

/** Group thinking, tools, and BUILD under the user prompt that started each turn. */
export function workTurnsFromMessages(messages: ChatMessage[]): WorkTurn[] {
  const turns: WorkTurn[] = []
  let current: WorkTurn | null = null
  for (const m of messages) {
    if (m.reverted) continue
    if (m.role === 'user') {
      current = {
        id: m.id,
        userMessageId: m.id,
        prompt: m.content || '',
        createdAt: m.created_at || '',
        assistantIds: [],
        thinking: '',
        steps: [],
      }
      turns.push(current)
      continue
    }
    if (m.role !== 'assistant') continue
    if (!current) {
      current = {
        id: m.id,
        userMessageId: null,
        prompt: '',
        createdAt: m.created_at || '',
        assistantIds: [],
        thinking: '',
        steps: [],
      }
      turns.push(current)
    }
    current.assistantIds.push(m.id)
    const thought = (m.thinking || '').trim()
    if (thought) {
      current.thinking = current.thinking ? `${current.thinking}\n\n${thought}` : thought
    }
    const steps = stepsFromMessageTools(m.tool_calls || [], m.tool_results || [], {
      requestId: m.request_id,
    })
    if (steps.length) current.steps = current.steps.concat(steps)
  }
  return turns
}

export function attachLiveWork(
  turns: WorkTurn[],
  live: {
    streaming: boolean
    thinking?: string
    steps?: ProcessStep[]
    todos?: BuildTodo[]
    lastUserId?: string | null
  },
): WorkTurn[] {
  const hasTodos = todosHaveOpen(live.todos)
  if (!live.streaming && !hasTodos && !(live.steps && live.steps.length) && !live.thinking?.trim()) {
    return turns.filter((t) => t.thinking.trim() || t.steps.length || todosHaveOpen(t.todos))
  }
  const next = turns.map((t) => ({ ...t, steps: t.steps.slice(), assistantIds: t.assistantIds.slice() }))
  let target =
    live.lastUserId != null ? next.find((t) => t.userMessageId === live.lastUserId) : undefined
  if (!target) {
    target = {
      id: live.lastUserId || 'live',
      userMessageId: live.lastUserId ?? null,
      prompt: '',
      createdAt: '',
      assistantIds: [],
      thinking: '',
      steps: [],
    }
    next.push(target)
  }
  if (live.thinking?.trim()) target.thinking = live.thinking
  if (live.steps && live.steps.length) target.steps = live.steps
  if (live.todos?.length) target.todos = live.todos
  target.live = live.streaming
  return next.filter(
    (t) => t.live || t.thinking.trim() || t.steps.length || todosHaveOpen(t.todos),
  )
}
