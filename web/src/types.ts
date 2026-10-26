export type ThemeMode = 'light' | 'dark' | 'system'

export type MessageContent =
  | string
  | {
      content?: string
      role?: string
      tool_calls?: Array<{
        id?: string
        type?: string
        function?: {
          name?: string
          arguments?: string
        }
      }>
      [key: string]: unknown
    }

export interface Chat {
  id: number
  project_id: number | null
  title: string
  created_at: string
  updated_at: string
  pinned: boolean
}

export interface ChatMessage {
  id: number | string
  chat_id: number
  role: 'user' | 'assistant' | 'tool' | string
  content: MessageContent
  created_at: string
  input_tokens: number | null
  output_tokens: number | null
}

export interface Project {
  id: number
  name: string
  root_path: string
  instructions: string
  created_at: string
}

export interface MemoryItem {
  id: number
  scope: 'global' | 'project'
  project_id: number | null
  content: string
  created_at: string
  updated_at: string
}

export interface SettingsMap {
  [key: string]: unknown
}

export interface ContextUsage {
  input_tokens: number
  output_tokens: number
  context_percent: number
  estimated: boolean
  output_estimated: boolean
}

export type PermissionDecision = 'allow_once' | 'allow_always' | 'deny'

export interface PermissionRequestEvent {
  type: 'permission_request'
  id: string
  tool: string
  args: Record<string, unknown>
  diff: string | null
}

export interface ToolCallEvent {
  type: 'tool_call'
  name: string
  args: Record<string, unknown>
}

export interface ToolResultEvent {
  type: 'tool_result'
  name: string
  output: string
  is_error: boolean
}

export interface UsageEvent {
  type: 'usage'
  input_tokens: number
  output_tokens: number
  context_percent: number
  estimated: boolean
  output_estimated: boolean
}

export type StreamEvent =
  | { type: 'text_delta'; text: string }
  | ToolCallEvent
  | ToolResultEvent
  | PermissionRequestEvent
  | UsageEvent
  | { type: 'error'; message: string }
  | { type: 'done'; cancelled: boolean }
