export interface ChatSession {
  id: string
  title: string
  model: string | null
  agent: string | null
  message_count: number
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  thinking: string | null
  tool_calls: ToolCall[]
  tool_results: ToolResult[]
  model: string | null
  agent: string | null
  tokens: number | null
  created_at: string
  reverted: boolean
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
}

export interface ToolResult {
  name: string
  output: string
  error?: string
}

export interface ModelDefinition {
  id: string
  name: string
  provider: string
  default: boolean
}

export interface AgentDefinition {
  name: string
  description: string
  build_mode: boolean
}

export interface CommandDefinition {
  name: string
  description: string
  aliases: string[]
  arguments: string | null
}

export interface SSEEvent {
  type: 'token' | 'thinking' | 'tool_call' | 'tool_result' | 'done' | 'error' | 'start'
  text?: string
  message?: string
  request_id?: string
  session_id?: string
}
