import type {
  Chat,
  ChatMessage,
  ContextUsage,
  MemoryItem,
  PermissionDecision,
  Project,
  SettingsMap,
  StreamEvent,
} from './types'

const TOKEN_STORAGE_KEY = 'shree-access-token'
const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8765'

let cachedToken: string | null = null

function getStoredToken(): string | null {
  if (typeof sessionStorage === 'undefined') {
    return null
  }

  return sessionStorage.getItem(TOKEN_STORAGE_KEY)
}

export function getAccessToken(): string | null {
  if (cachedToken) {
    return cachedToken
  }

  const fromStorage = getStoredToken()
  if (fromStorage) {
    cachedToken = fromStorage
    return cachedToken
  }

  if (typeof window === 'undefined') {
    return null
  }

  const urlToken = new URLSearchParams(window.location.search).get('token')
  if (urlToken) {
    setAccessToken(urlToken)
    return urlToken
  }

  return null
}

export function setAccessToken(token: string): void {
  cachedToken = token
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
  }
}

export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_API_BASE_URL

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken()
  if (!token) {
    throw new Error('Missing access token. Append ?token=<value> to the app URL.')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Access-Token': token,
      ...(options.headers ?? {}),
    },
  })

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) {
        message = payload.detail
      }
    } catch {
      // Ignore JSON decode errors; keep the default message.
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    return (await response.json()) as T
  }

  return (await response.text()) as T
}

export const api = {
  listChats: async (search = ''): Promise<Chat[]> => {
    const query = search ? `?search=${encodeURIComponent(search)}` : ''
    return apiFetch<Chat[]>(`/api/chats${query}`)
  },

  createChat: async (payload: { project_id?: number | null; title?: string }): Promise<Chat> =>
    apiFetch<Chat>('/api/chats', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  renameChat: async (chatId: number, title: string): Promise<Chat> =>
    apiFetch<Chat>(`/api/chats/${chatId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  pinChat: async (chatId: number, pinned: boolean): Promise<Chat> =>
    apiFetch<Chat>(`/api/chats/${chatId}/pin`, {
      method: 'PUT',
      body: JSON.stringify({ pinned }),
    }),

  deleteChat: async (chatId: number): Promise<void> => {
    await apiFetch<void>(`/api/chats/${chatId}`, {
      method: 'DELETE',
    })
  },

  exportChatMarkdown: async (chatId: number): Promise<string> => {
    const token = getAccessToken()
    if (!token) {
      throw new Error('Missing access token.')
    }

    const response = await fetch(`${API_BASE_URL}/api/chats/${chatId}/export`, {
      headers: {
        'X-Access-Token': token,
      },
    })

    if (!response.ok) {
      throw new Error('Unable to export chat.')
    }

    return response.text()
  },

  listChatMessages: async (chatId: number): Promise<ChatMessage[]> =>
    apiFetch<ChatMessage[]>(`/api/chats/${chatId}/messages`),

  streamChatTurn: async (
    chatId: number,
    content: string,
    onEvent: (event: StreamEvent) => void,
  ): Promise<void> => {
    const token = getAccessToken()
    if (!token) {
      throw new Error('Missing access token. Append ?token=<value> to the app URL.')
    }

    const response = await fetch(`${API_BASE_URL}/api/chats/${chatId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Access-Token': token,
      },
      body: JSON.stringify({ content }),
    })

    if (!response.ok) {
      let message = `Request failed (${response.status})`
      try {
        const payload = (await response.json()) as { detail?: string }
        if (payload.detail) {
          message = payload.detail
        }
      } catch {
        // ignore parse errors
      }
      throw new Error(message)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('The server did not return a readable stream.')
    }

    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent: { name: string | null; data: string } = { name: null, data: '' }

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split(/\r?\n\r?\n/)
      buffer = chunks.pop() ?? ''

      for (const chunk of chunks) {
        const lines = chunk.split(/\r?\n/)
        currentEvent = { name: null, data: '' }

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) {
            continue
          }

          if (trimmed.startsWith('event:')) {
            currentEvent.name = trimmed.slice('event:'.length).trim()
            continue
          }

          if (trimmed.startsWith('data:') || trimmed.startsWith('"data":')) {
            currentEvent.data = trimmed
              .replace(/^"?data"?\s*:\s*/, '')
              .trim()
            continue
          }
        }

        if (!currentEvent.data) {
          continue
        }

        try {
          const payload = JSON.parse(currentEvent.data) as StreamEvent
          onEvent(payload)
        } catch {
          // Ignore fragments that are not valid JSON yet.
        }
      }
    }

    if (buffer.trim()) {
      const lines = buffer.split(/\r?\n/)
      let data = ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        if (trimmed.startsWith('event:')) {
          continue
        }
        if (trimmed.startsWith('data:') || trimmed.startsWith('"data":')) {
          data = trimmed.replace(/^"?data"?\s*:\s*/, '').trim()
        }
      }
      if (data) {
        try {
          const payload = JSON.parse(data) as StreamEvent
          onEvent(payload)
        } catch {
          // ignore incomplete final frame
        }
      }
    }
  },

  stopChat: async (chatId: number): Promise<{ stopping: boolean }> =>
    apiFetch<{ stopping: boolean }>(`/api/chats/${chatId}/stop`, {
      method: 'POST',
    }),

  getContextUsage: async (chatId: number): Promise<ContextUsage> =>
    apiFetch<ContextUsage>(`/api/context/${chatId}`),

  submitPermission: async (requestId: string, decision: PermissionDecision): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(`/api/permissions/${requestId}`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),

  listProjects: async (): Promise<Project[]> => apiFetch<Project[]>('/api/projects'),

  createProject: async (payload: { name: string; root_path: string; instructions?: string }): Promise<Project> =>
    apiFetch<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateProject: async (
    projectId: number,
    payload: { name?: string; root_path?: string; instructions?: string },
  ): Promise<Project> =>
    apiFetch<Project>(`/api/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteProject: async (projectId: number): Promise<void> => {
    await apiFetch<void>(`/api/projects/${projectId}`, {
      method: 'DELETE',
    })
  },

  listMemories: async (scope?: string, projectId?: number): Promise<MemoryItem[]> => {
    const params = new URLSearchParams()
    if (scope) {
      params.set('scope', scope)
    }
    if (projectId !== undefined) {
      params.set('project_id', String(projectId))
    }
    const suffix = params.toString() ? `?${params.toString()}` : ''
    return apiFetch<MemoryItem[]>(`/api/memory${suffix}`)
  },

  createMemory: async (payload: { scope: string; project_id?: number | null; content: string }): Promise<MemoryItem> =>
    apiFetch<MemoryItem>('/api/memory', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateMemory: async (memoryId: number, content: string): Promise<MemoryItem> =>
    apiFetch<MemoryItem>(`/api/memory/${memoryId}`, {
      method: 'PATCH',
      body: JSON.stringify({ content }),
    }),

  deleteMemory: async (memoryId: number): Promise<void> => {
    await apiFetch<void>(`/api/memory/${memoryId}`, {
      method: 'DELETE',
    })
  },

  getSettings: async (): Promise<SettingsMap> => apiFetch<SettingsMap>('/api/settings'),

  updateSettings: async (settings: SettingsMap): Promise<SettingsMap> =>
    apiFetch<SettingsMap>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ settings }),
    }),
}
