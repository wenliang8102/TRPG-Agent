// frontend/src/Services_/sessionService.ts

const SESSION_STORAGE_KEY = 'trpg-chat-session-id'
const PENDING_SESSION_STORAGE_KEY = 'pending_session_id'

export interface ChatSession {
  id: string
  title: string
  createdAt: number
  lastMessageAt: number
  preview?: string
  messageCount: number
}

export async function listSessions(): Promise<ChatSession[]> {
  const response = await fetch('/api/sessions')
  if (!response.ok) {
    throw new Error(`获取会话列表失败: ${response.status}`)
  }

  const data = await response.json()
  return data.sessions ?? []
}

export async function createSession(title?: string): Promise<ChatSession> {
  const response = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!response.ok) {
    throw new Error(`创建会话失败: ${response.status}`)
  }

  return await response.json()
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(`删除会话失败: ${response.status}`)
  }

  if (localStorage.getItem(SESSION_STORAGE_KEY) === sessionId) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
  }
  if (sessionStorage.getItem(PENDING_SESSION_STORAGE_KEY) === sessionId) {
    sessionStorage.removeItem(PENDING_SESSION_STORAGE_KEY)
  }

  return true
}
