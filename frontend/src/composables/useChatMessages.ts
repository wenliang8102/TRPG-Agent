import { ref } from 'vue'
import type { ChatMessage, PendingAction } from '../Services_/chatService'

export function useChatMessages() {
  const messages = ref<ChatMessage[]>([
    { role: 'assistant', content: '你好，我是 TRPG 助手。你可以直接开始提问。' }
  ])
  const pendingAction = ref<PendingAction | null>(null)
  const errorText = ref('')
  const isSending = ref(false)
  const playerState = ref<any | null>(null)
  const combatState = ref<any | null>(null)

  const addUserMessage = (content: string) => {
    messages.value.push({ role: 'user', content })
  }

  const addAssistantMessage = (content: string) => {
    if (content.trim()) {
      messages.value.push({ role: 'assistant', content })
    }
  }

  const addConfirmedMessage = (reason?: string) => {
    messages.value.push({ role: 'user', content: `[掷骰确认: ${reason || '无'}]` })
  }

  const setPendingAction = (action: PendingAction | null) => {
    pendingAction.value = action
  }

  const setError = (error: string) => {
    errorText.value = error
  }

  const setSending = (sending: boolean) => {
    isSending.value = sending
  }

  const clearError = () => {
    errorText.value = ''
  }

  const setPlayerState = (state: any) => {
    playerState.value = state
  }

  const setCombatState = (state: any) => {
    combatState.value = state
  }

  return {
    messages,
    pendingAction,
    errorText,
    isSending,
    playerState,
    combatState,
    addUserMessage,
    addAssistantMessage,
    addConfirmedMessage,
    setPendingAction,
    setError,
    setSending,
    clearError,
    setPlayerState,
    setCombatState
  }
}