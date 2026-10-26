import { lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
const CodeBlock = lazy(() => import('./CodeBlock'))
import type {
  Chat,
  ChatMessage,
  ContextUsage,
  MessageContent,
  PermissionDecision,
  PermissionRequestEvent,
  StreamEvent,
  ThemeMode,
} from './types'

type DateGroupKey = 'pinned' | 'today' | 'yesterday' | 'last7' | 'older'

type DateSection = {
  key: DateGroupKey
  label: string
  chats: Chat[]
}

type ToolCardState = {
  id: string
  name: string
  args: Record<string, unknown>
  output: string
  isError: boolean
  status: 'running' | 'done'
  collapsed: boolean
}

const THEME_STORAGE_KEY = 'shree-theme'

function isDarkMode(theme: ThemeMode): boolean {
  if (theme === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }

  return theme === 'dark'
}

function getChatDateGroup(chat: Chat): DateGroupKey {
  const updatedAt = new Date(chat.updated_at).getTime()
  const now = Date.now()
  const dayMs = 1000 * 60 * 60 * 24
  const diffDays = Math.floor((now - updatedAt) / dayMs)

  if (diffDays <= 0) {
    return 'today'
  }

  if (diffDays === 1) {
    return 'yesterday'
  }

  if (diffDays <= 7) {
    return 'last7'
  }

  return 'older'
}

function getMarkdownFileName(chat: Chat | undefined, chatId: number): string {
  const title = chat?.title ?? `chat-${chatId}`
  const safeTitle = Array.from(title)
    .filter((character) => character.charCodeAt(0) >= 32)
    .join('')
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/\s+/g, '-')
    .trim()

  return `${safeTitle || `chat-${chatId}`}.md`
}

function getMessageText(message: MessageContent | undefined): string {
  if (typeof message === 'string') {
    return message
  }

  if (!message || typeof message !== 'object') {
    return ''
  }

  if (typeof message.content === 'string') {
    return message.content
  }

  return JSON.stringify(message, null, 2)
}

function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [renameTarget, setRenameTarget] = useState<Chat | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Chat | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null
    return stored ?? 'system'
  })
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if ((event.metaKey || event.ctrlKey) && key === 'k') {
        event.preventDefault()
        searchInputRef.current?.focus()
      }

      if ((event.metaKey || event.ctrlKey) && event.shiftKey && key === 'o') {
        event.preventDefault()
        void createChatMutation.mutate()
      }
    }

    window.addEventListener('keydown', handleShortcut as unknown as EventListener)
    return () => window.removeEventListener('keydown', handleShortcut as unknown as EventListener)
  }, [])

  const { data: chats = [], isLoading } = useQuery({
    queryKey: ['chats', search],
    queryFn: () => api.listChats(search),
  })

  const createChatMutation = useMutation({
    mutationFn: () => api.createChat({ title: 'New chat' }),
    onSuccess: (newChat) => {
      void queryClient.invalidateQueries({ queryKey: ['chats'] })
      setMobileSidebarOpen(false)
      navigate(`/chat/${newChat.id}`)
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const renameChatMutation = useMutation({
    mutationFn: ({ chatId, title }: { chatId: number; title: string }) =>
      api.renameChat(chatId, title),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['chats'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const pinChatMutation = useMutation({
    mutationFn: ({ chatId, pinned }: { chatId: number; pinned: boolean }) =>
      api.pinChat(chatId, pinned),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['chats'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const deleteChatMutation = useMutation({
    mutationFn: (chatId: number) => api.deleteChat(chatId),
    onSuccess: (_result, chatId) => {
      void queryClient.invalidateQueries({ queryKey: ['chats'] })
      setDeleteTarget(null)
      if (location.pathname === `/chat/${chatId}`) {
        navigate('/')
      }
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const exportChatMutation = useMutation({
    mutationFn: (chatId: number) => api.exportChatMarkdown(chatId),
    onSuccess: (markdown, chatId) => {
      const chat = chats.find((item) => item.id === chatId)
      const fileName = getMarkdownFileName(chat, chatId)
      const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }))
      const link = document.createElement('a')
      link.href = url
      link.download = fileName
      link.click()
      URL.revokeObjectURL(url)
    },
    onError: (error: Error) => setActionError(error.message),
  })

  useEffect(() => {
    const dark = isDarkMode(theme)
    document.documentElement.classList.toggle('dark', dark)
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  useEffect(() => {
    if (theme !== 'system') {
      return
    }

    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const updateTheme = (event: MediaQueryListEvent) => {
      document.documentElement.classList.toggle('dark', event.matches)
      document.documentElement.style.colorScheme = event.matches ? 'dark' : 'light'
    }
    media.addEventListener('change', updateTheme)
    return () => media.removeEventListener('change', updateTheme)
  }, [theme])

  const chatSections = useMemo<DateSection[]>(() => {
    const normalized = [...chats].sort((a, b) => {
      const aTime = new Date(a.updated_at).getTime()
      const bTime = new Date(b.updated_at).getTime()
      return bTime - aTime
    })

    const pinned = normalized.filter((chat) => chat.pinned)
    const rest = normalized.filter((chat) => !chat.pinned)

    const sections: DateSection[] = [
      { key: 'today', label: 'Today', chats: [] },
      { key: 'yesterday', label: 'Yesterday', chats: [] },
      { key: 'last7', label: 'Previous 7 days', chats: [] },
      { key: 'older', label: 'Older', chats: [] },
    ]

    for (const chat of rest) {
      const group = getChatDateGroup(chat)
      const section = sections.find((item) => item.key === group)
      if (section) {
        section.chats.push(chat)
      }
    }

    return [{ key: 'pinned', label: 'Pinned', chats: pinned }, ...sections]
  }, [chats])

  const handleRename = (chat: Chat) => {
    setRenameTarget(chat)
    setRenameTitle(chat.title)
  }

  const handleDelete = (chat: Chat) => {
    setDeleteTarget(chat)
  }

  const submitRename = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const title = renameTitle.trim()
    if (!renameTarget || !title) {
      return
    }

    renameChatMutation.mutate(
      { chatId: renameTarget.id, title },
      {
        onSuccess: () => setRenameTarget(null),
      },
    )
  }

  const handleThemeCycle = () => {
    setTheme((current) => {
      if (current === 'light') {
        return 'dark'
      }

      if (current === 'dark') {
        return 'system'
      }

      return 'light'
    })
  }

  const navItems = [
    { to: '/', label: 'Chats' },
    { to: '/projects', label: 'Projects' },
    { to: '/memory', label: 'Memory' },
    { to: '/settings', label: 'Settings' },
  ]

  return (
    <div className="flex h-screen bg-slate-100 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
      {mobileSidebarOpen && (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-slate-950/40 md:hidden"
          onClick={() => setMobileSidebarOpen(false)}
          aria-label="Close navigation"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex h-full w-[320px] flex-col border-r border-slate-200 bg-white/95 shadow-xl backdrop-blur-xl transition-all duration-200 dark:border-slate-800 dark:bg-slate-900/95 md:relative md:z-auto md:shadow-none ${
          mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
        } ${sidebarOpen ? 'md:w-[320px]' : 'md:w-[88px]'}`}
      >
        <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-3 dark:border-slate-800">
          <button
            type="button"
            onClick={() => void createChatMutation.mutate()}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-slate-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-slate-700 dark:bg-sky-500 dark:text-slate-950 dark:hover:bg-sky-400"
          >
            <span className="text-lg leading-none">+</span>
            {(sidebarOpen || mobileSidebarOpen) && <span>New chat</span>}
          </button>
          <button
            type="button"
            onClick={() => setSidebarOpen((value) => !value)}
            className="hidden rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 md:block"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? '←' : '→'}
          </button>
          <button
            type="button"
            onClick={() => setMobileSidebarOpen(false)}
            className="rounded-lg border border-slate-200 px-2 py-1 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 md:hidden"
            aria-label="Close sidebar"
          >
            ×
          </button>
        </div>

        <div className={`${sidebarOpen || mobileSidebarOpen ? 'block' : 'hidden'} px-3 pb-3 pt-3`}>
          <label className="relative block">
            <span className="sr-only">Search chats</span>
            <input
              ref={searchInputRef}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search chats"
              className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 outline-none ring-0 transition placeholder:text-slate-400 focus:border-sky-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500"
            />
          </label>
        </div>

        <nav className="space-y-1 border-b border-slate-200 px-3 pb-3 dark:border-slate-800 md:hidden">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setMobileSidebarOpen(false)}
              className={({ isActive }) =>
                `block rounded-xl px-3 py-2 text-sm ${
                  isActive
                    ? 'bg-slate-900 text-white dark:bg-sky-500 dark:text-slate-950'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="flex-1 overflow-y-auto px-3 pb-4">
          {isLoading ? (
            <div className="space-y-2">
              <div className="h-4 w-24 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
              <div className="h-12 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-700" />
            </div>
          ) : (
            <div className="space-y-4">
              {chatSections.map((section) => {
                if (section.chats.length === 0) {
                  return null
                }

                return (
                  <div key={section.key} className="space-y-2">
                    {(sidebarOpen || mobileSidebarOpen) && (
                      <div className="px-2 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400 dark:text-slate-500">
                        {section.label}
                      </div>
                    )}

                    {section.chats.map((chat) => (
                      <ChatRow
                        key={chat.id}
                        chat={chat}
                        compact={!sidebarOpen && !mobileSidebarOpen}
                        onRename={handleRename}
                        onDelete={handleDelete}
                        onExport={(chatId) => exportChatMutation.mutate(chatId)}
                        onPin={(pinned) => pinChatMutation.mutate({ chatId: chat.id, pinned })}
                        onNavigate={() => setMobileSidebarOpen(false)}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white/80 px-5 py-3 backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/80">
          <button
            type="button"
            onClick={() => setMobileSidebarOpen(true)}
            className="mr-2 rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 dark:border-slate-700 dark:text-slate-300 md:hidden"
            aria-label="Open sidebar"
          >
            ☰
          </button>
          <nav className="hidden items-center gap-2 text-sm sm:flex">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setMobileSidebarOpen(false)}
                className={({ isActive }) =>
                  `rounded-full px-3 py-1.5 transition ${
                    isActive
                      ? 'bg-slate-900 text-white dark:bg-sky-500 dark:text-slate-950'
                      : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <button
            type="button"
            onClick={handleThemeCycle}
            className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1.5 text-sm text-slate-700 transition hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            {theme === 'light' ? 'Light' : theme === 'dark' ? 'Dark' : 'System'}
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          {actionError && (
            <div
              role="alert"
              className="mx-auto mb-4 flex max-w-3xl items-center justify-between gap-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300"
            >
              <span>{actionError}</span>
              <button type="button" onClick={() => setActionError(null)} aria-label="Dismiss error">
                ×
              </button>
            </div>
          )}
          <Routes>
            <Route path="/" element={<HomePage chats={chats} onCreateChat={() => void createChatMutation.mutate()} />} />
            <Route path="/chat/:chatId" element={<ChatPage />} />
            <Route path="/projects" element={<PlaceholderPage title="Projects" description="Project management is coming soon." />} />
            <Route path="/memory" element={<PlaceholderPage title="Memory" description="Shared memory and project notes are coming soon." />} />
            <Route path="/settings" element={<PlaceholderPage title="Settings" description="Preferences and model configuration are coming soon." />} />
          </Routes>
        </div>
      </main>
      {renameTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-chat-title"
            onSubmit={submitRename}
            className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
          >
            <h2 id="rename-chat-title" className="text-lg font-semibold">Rename chat</h2>
            <label className="mt-4 block text-sm text-slate-600 dark:text-slate-300">
              Chat title
              <input
                autoFocus
                value={renameTitle}
                onChange={(event) => setRenameTitle(event.target.value)}
                className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
            </label>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setRenameTarget(null)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-sm dark:border-slate-700"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!renameTitle.trim() || renameChatMutation.isPending}
                className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-sky-500 dark:text-slate-950"
              >
                {renameChatMutation.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      )}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-chat-title"
            className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
          >
            <h2 id="delete-chat-title" className="text-lg font-semibold">Delete chat?</h2>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              “{deleteTarget.title}” and its conversation history will be deleted.
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-sm dark:border-slate-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleteChatMutation.isPending}
                onClick={() => deleteChatMutation.mutate(deleteTarget.id)}
                className="rounded-xl bg-rose-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 hover:bg-rose-700"
              >
                {deleteChatMutation.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

type ChatRowProps = {
  chat: Chat
  compact: boolean
  onRename: (chat: Chat) => void
  onDelete: (chat: Chat) => void
  onExport: (chatId: number) => void
  onPin: (pinned: boolean) => void
  onNavigate: () => void
}

function ChatRow({ chat, compact, onRename, onDelete, onExport, onPin, onNavigate }: ChatRowProps) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="group relative">
      <Link
        to={`/chat/${chat.id}`}
        onClick={onNavigate}
        className="flex items-center gap-3 rounded-xl border border-transparent px-2 py-2 transition hover:border-slate-200 hover:bg-slate-100 dark:hover:border-slate-700 dark:hover:bg-slate-800"
      >
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-500 text-sm font-semibold text-white shadow-sm">
          {chat.title.charAt(0).toUpperCase() || 'C'}
        </div>

        {!compact && (
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                {chat.title}
              </span>
              {chat.pinned && <span className="text-xs text-amber-500">★</span>}
            </div>
            <div className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
              {new Date(chat.updated_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
              })}
            </div>
          </div>
        )}
      </Link>

      {!compact && (
        <div className="absolute right-2 top-2">
          <button
            type="button"
            onClick={() => setMenuOpen((value) => !value)}
            className="rounded-md p-1 text-slate-500 hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-700"
            aria-label={`Chat actions for ${chat.title}`}
          >
            ⋯
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-8 z-10 min-w-40 rounded-xl border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  onRename(chat)
                }}
                className="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                Rename
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  onPin(!chat.pinned)
                }}
                className="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                {chat.pinned ? 'Unpin' : 'Pin'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  onExport(chat.id)
                }}
                className="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                Export
              </button>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false)
                  onDelete(chat)
                }}
                className="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
              >
                Delete
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

type HomePageProps = {
  chats: Chat[]
  onCreateChat: () => void
}

function HomePage({ chats, onCreateChat }: HomePageProps) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="max-w-xl rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-900 text-2xl text-white dark:bg-sky-500 dark:text-slate-950">
          ✦
        </div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">Welcome back</h1>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          {chats.length > 0
            ? 'Choose a chat from the sidebar or start a fresh conversation.'
            : 'Your conversations will appear here once you start chatting.'}
        </p>
        <button
          type="button"
          onClick={onCreateChat}
          className="mt-6 inline-flex items-center rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 dark:bg-sky-500 dark:text-slate-950 dark:hover:bg-sky-400"
        >
          Start a new chat
        </button>
      </div>
    </div>
  )
}

function ChatPage() {
  const { chatId } = useParams()
  const numericChatId = Number(chatId)
  const queryClient = useQueryClient()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null)
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequestEvent | null>(null)
  const [toolCards, setToolCards] = useState<ToolCardState[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [statusText, setStatusText] = useState('Ready')

  const { data: persistedMessages = [], isLoading } = useQuery({
    queryKey: ['chatMessages', numericChatId],
    queryFn: () => api.listChatMessages(numericChatId),
    enabled: Number.isFinite(numericChatId),
  })

  const { data: usageData } = useQuery({
    queryKey: ['contextUsage', numericChatId],
    queryFn: () => api.getContextUsage(numericChatId),
    enabled: Number.isFinite(numericChatId),
  })

  useEffect(() => {
    setMessages(persistedMessages)
  }, [persistedMessages])

  useEffect(() => {
    if (usageData) {
      setContextUsage(usageData)
    }
  }, [usageData])

  useEffect(() => {
    if (isAtBottom && containerRef.current) {
      containerRef.current.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
    }
  }, [messages, permissionRequest, toolCards, isStreaming, isAtBottom])

  const sendMessage = async (content: string) => {
    const trimmed = content.trim()
    if (!trimmed || !Number.isFinite(numericChatId) || isStreaming) {
      return
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      chat_id: numericChatId,
      role: 'user',
      content: trimmed,
      created_at: new Date().toISOString(),
      input_tokens: null,
      output_tokens: null,
    }

    const assistantMessageId = `assistant-${Date.now()}`
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      chat_id: numericChatId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      input_tokens: null,
      output_tokens: null,
    }

    setStreamError(null)
    setPermissionRequest(null)
    setStatusText('Generating…')
    setIsStreaming(true)
    setMessages((current) => [...current, userMessage, assistantMessage])
    setDraft('')

    try {
      await api.streamChatTurn(numericChatId, trimmed, (event: StreamEvent) => {
        if (event.type === 'text_delta') {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantMessageId
                ? {
                    ...message,
                    content: `${getMessageText(message.content)}${event.text}`,
                  }
                : message,
            ),
          )
          return
        }

        if (event.type === 'tool_call') {
          setToolCards((current) => [
            {
              id: `${event.name}-${Date.now()}`,
              name: event.name,
              args: event.args,
              output: '',
              isError: false,
              status: 'running',
              collapsed: false,
            },
            ...current,
          ])
          return
        }

        if (event.type === 'tool_result') {
          setToolCards((current) =>
            current.map((card) =>
              card.name === event.name && card.status === 'running'
                ? { ...card, output: event.output, isError: event.is_error, status: 'done' }
                : card,
            ),
          )
          return
        }

        if (event.type === 'permission_request') {
          setPermissionRequest(event)
          setStatusText('Waiting for permission…')
          return
        }

        if (event.type === 'usage') {
          setContextUsage(event)
          return
        }

        if (event.type === 'error') {
          setStreamError(event.message)
          setStatusText('The assistant hit an error.')
          return
        }

        if (event.type === 'done') {
          setStatusText(event.cancelled ? 'Stopped.' : 'Ready')
        }
      })
    } catch (error) {
      setStreamError(error instanceof Error ? error.message : 'The server dropped the connection.')
      setStatusText('Connection interrupted')
    } finally {
      setIsStreaming(false)
      setPermissionRequest(null)
      void queryClient.invalidateQueries({ queryKey: ['chatMessages', numericChatId] })
      void queryClient.invalidateQueries({ queryKey: ['contextUsage', numericChatId] })
    }
  }

  const handlePermissionDecision = async (decision: PermissionDecision) => {
    if (!permissionRequest) {
      return
    }

    setStatusText('Permission submitted…')
    await api.submitPermission(permissionRequest.id, decision)
    setPermissionRequest(null)
    setStatusText('Generating…')
  }

  const stopStreaming = async () => {
    if (!Number.isFinite(numericChatId)) {
      return
    }

    try {
      await api.stopChat(numericChatId)
      setStatusText('Stopping…')
    } catch (error) {
      setStreamError(error instanceof Error ? error.message : 'Unable to stop the generation.')
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void sendMessage(draft)
    }
  }

  const messageList = useMemo(() => {
    return messages.filter((item) => item.role !== 'system')
  }, [messages])

  const hasMessages = messageList.length > 0

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-2 pb-3 dark:border-slate-800">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Conversation</p>
          <h1 className="mt-1 text-xl font-semibold text-slate-900 dark:text-white">{Number.isFinite(numericChatId) ? `Chat #${numericChatId}` : 'New chat'}</h1>
        </div>
        {contextUsage && (
          <div className="flex w-full max-w-xs flex-col gap-2 text-right text-xs text-slate-500 dark:text-slate-400">
            <div className="flex items-center justify-between gap-2">
              <span>Context</span>
              <span>{contextUsage.input_tokens.toLocaleString()} tokens</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-sky-500 to-indigo-500"
                style={{ width: `${Math.min(contextUsage.context_percent, 100)}%` }}
              />
            </div>
            <span>{Math.min(Math.round(contextUsage.context_percent), 100)}% of window</span>
          </div>
        )}
      </div>

      <div ref={containerRef} className="flex-1 space-y-5 overflow-y-auto px-2 py-5">
        {isLoading ? (
          <div className="space-y-4">
            <div className="h-12 w-full animate-pulse rounded-2xl bg-slate-200 dark:bg-slate-800" />
            <div className="h-24 w-3/4 animate-pulse rounded-2xl bg-slate-200 dark:bg-slate-800" />
          </div>
        ) : !hasMessages ? (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-xl rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-500 text-2xl text-white">✦</div>
              <h2 className="text-2xl font-semibold text-slate-900 dark:text-white">Start a new conversation</h2>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                Ask about the codebase, generate files, or troubleshoot a bug.
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {['Review the current architecture', 'Find a bug in the app', 'Draft a small feature plan'].map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setDraft(example)}
                    className="rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          messageList.map((message) => (
            <MessageBubble key={String(message.id)} message={message} />
          ))
        )}

        {toolCards.length > 0 && (
          <div className="space-y-3">
            {toolCards.map((tool) => (
              <ToolCallCard
                key={tool.id}
                tool={tool}
                onToggle={() =>
                  setToolCards((current) =>
                    current.map((card) =>
                      card.id === tool.id ? { ...card, collapsed: !card.collapsed } : card,
                    ),
                  )
                }
              />
            ))}
          </div>
        )}

        {permissionRequest && (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-900/60 dark:bg-amber-950/30">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-[0.2em] text-amber-700 dark:text-amber-300">Permission requested</p>
                <h3 className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">{permissionRequest.tool}</h3>
              </div>
            </div>
            <pre className="mt-3 max-h-48 overflow-auto rounded-xl bg-slate-900 p-3 text-xs text-slate-100">
              {permissionRequest.diff || JSON.stringify(permissionRequest.args, null, 2)}
            </pre>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handlePermissionDecision('allow_once')}
                className="rounded-xl bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500"
              >
                Allow once
              </button>
              <button
                type="button"
                onClick={() => void handlePermissionDecision('allow_always')}
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                Always allow
              </button>
              <button
                type="button"
                onClick={() => void handlePermissionDecision('deny')}
                className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 hover:bg-rose-100 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300"
              >
                Deny
              </button>
            </div>
          </div>
        )}

        {streamError && (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
            {streamError}
          </div>
        )}

        {!isAtBottom && (
          <button
            type="button"
            onClick={() => {
              setIsAtBottom(true)
              if (containerRef.current) {
                containerRef.current.scrollTo({
                  top: containerRef.current.scrollHeight,
                  behavior: 'smooth',
                })
              }
            }}
            className="fixed bottom-28 right-6 rounded-full bg-slate-900 px-3 py-2 text-sm font-medium text-white shadow-xl dark:bg-sky-500 dark:text-slate-950"
          >
            Jump to latest
          </button>
        )}
      </div>

      <div className="border-t border-slate-200 bg-white/80 p-3 backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto max-w-5xl rounded-2xl border border-slate-200 bg-slate-50 p-3 shadow-sm dark:border-slate-700 dark:bg-slate-800/70">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message the assistant…"
            rows={3}
            className="w-full resize-none border-0 bg-transparent px-2 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
          />

          <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-200 pt-2 dark:border-slate-700">
            <div className="text-xs text-slate-500 dark:text-slate-400">{statusText}</div>
            <div className="flex items-center gap-2">
              {isStreaming ? (
                <button
                  type="button"
                  onClick={() => void stopStreaming()}
                  className="rounded-xl bg-rose-600 px-3 py-2 text-sm font-medium text-white hover:bg-rose-500"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void sendMessage(draft)}
                  disabled={!draft.trim()}
                  className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-sky-500 dark:text-slate-950"
                >
                  Send
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

type MessageBubbleProps = {
  message: ChatMessage
}

function MessageBubble({ message }: MessageBubbleProps) {
  const isAssistant = message.role === 'assistant'
  const content = getMessageText(message.content)
  const textClass = isAssistant ? 'text-slate-800 dark:text-slate-100' : 'text-slate-900 dark:text-white'

  return (
    <div className={`flex ${isAssistant ? 'justify-start' : 'justify-end'}`}>
      <div className={`w-full max-w-3xl rounded-2xl border p-4 ${isAssistant ? 'border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900' : 'border-sky-200 bg-sky-50 dark:border-sky-900 dark:bg-sky-950/30'}`}>
        <div className="mb-2 flex items-center justify-between gap-2 text-[11px] uppercase tracking-[0.18em] text-slate-400 dark:text-slate-500">
          <span>{isAssistant ? 'Assistant' : 'You'}</span>
          {isAssistant && (
            <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
              <button type="button" className="hover:text-slate-700 dark:hover:text-slate-200">Copy</button>
              <button type="button" className="hover:text-slate-700 dark:hover:text-slate-200">Regenerate</button>
            </div>
          )}
        </div>
        <div className={`prose prose-slate max-w-none prose-headings:mt-4 prose-p:my-2 prose-code:before:hidden prose-code:after:hidden dark:prose-invert ${textClass}`}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ inline, className, children, ...props }: any) {
                const codeText = String(children).replace(/\n$/, '')
                if (inline) {
                  return <code className={className} {...props}>{children}</code>
                }

                return (
                  <Suspense fallback={<pre><code>{codeText}</code></pre>}>
                    <CodeBlock className={className} code={codeText} />
                  </Suspense>
                )
              },
            }}
          >
            {content || ' '}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

type ToolCallCardProps = {
  tool: ToolCardState
  onToggle: () => void
}

function ToolCallCard({ tool, onToggle }: ToolCallCardProps) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <button type="button" onClick={onToggle} className="flex w-full items-center justify-between gap-3 text-left">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-xs font-semibold text-white dark:bg-sky-500 dark:text-slate-950">
            {tool.status === 'running' ? '…' : tool.isError ? '!' : '✓'}
          </div>
          <div>
            <div className="text-sm font-medium text-slate-900 dark:text-white">{tool.name}</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">{tool.status === 'running' ? 'Running…' : tool.isError ? 'Error' : 'Completed'}</div>
          </div>
        </div>
        <span className="text-slate-500 dark:text-slate-400">{tool.collapsed ? 'Show' : 'Hide'}</span>
      </button>
      {!tool.collapsed && (
        <div className="mt-3 space-y-3">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-[0.18em] text-slate-400">Arguments</div>
            <pre className="rounded-xl bg-slate-100 p-3 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              {JSON.stringify(tool.args, null, 2)}
            </pre>
          </div>
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-[0.18em] text-slate-400">Result</div>
            <pre className="max-h-56 overflow-auto rounded-xl bg-slate-100 p-3 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              {tool.output || 'No result yet.'}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

type PlaceholderPageProps = {
  title: string
  description: string
}

function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div className="mx-auto max-w-3xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">{title}</h1>
      <p className="mt-3 text-slate-600 dark:text-slate-300">{description}</p>
    </div>
  )
}

export default App
