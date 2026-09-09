export interface ChatSession {
  id: string
  title: string
  model: string | null
  agent: string | null
  project_path?: string | null
  /** Per-session provider override */
  llm_provider?: string | null
  message_count: number
  /** Messenger origin when session started on Telegram/Discord/etc. */
  origin_channel?: string | null
  external_chat_id?: string | null
  external_user?: string | null
  created_at: string
  updated_at: string
  /** True while a turn holds this session's stream claim (server-side work in flight). */
  claimed?: boolean
  /** Turn id of that claim — what `stream/attach` and the evidence route are keyed on. */
  active_request_id?: string | null
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
  /** Runtime call id — pairs results with calls when tools run in parallel. */
  id?: string
}

export interface ToolResult {
  name: string
  output: string
  error?: string
  /** Matches `ToolCall.id`; absent on rows persisted by older runtimes. */
  id?: string
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
  claim_epoch?: number
}
