import { ref } from 'vue'

const SESSION_STORAGE_KEY = 'trpg-chat-session-id'

const getStoredSessionId = (): string | null => {
  const value = localStorage.getItem(SESSION_STORAGE_KEY)
  return value && value.trim() ? value : null
}

const setStoredSessionId = (sessionId: string) => {
  localStorage.setItem(SESSION_STORAGE_KEY, sessionId)
}

export function useChatSession() {
  const sessionId = ref<string | null>(getStoredSessionId())

  const updateSessionId = (newId: string) => {
    if (newId && newId !== sessionId.value) {
      sessionId.value = newId
      setStoredSessionId(newId)
    }
  }

  return { sessionId, updateSessionId }
}