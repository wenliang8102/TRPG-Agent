// frontend/src/composables/useChatMessages.ts
import { ref } from 'vue'
import type { ChatMessage, PendingAction, HpChange } from '../Services_/chatService'

const createMessage = (
  role: ChatMessage['role'],
  content: string,
  extras: Partial<ChatMessage> = {}
): ChatMessage => ({
  id: crypto.randomUUID(),
  role,
  content,
  timestamp: Date.now(),
  isHistory: false,   // 新消息默认不是历史
  ...extras,
})

export function useChatMessages() {
  const messages = ref<ChatMessage[]>([
    createMessage('assistant', '你好，我是 TRPG 助手。你可以直接开始提问。')
  ])
  const pendingAction = ref<PendingAction | null>(null)
  const errorText = ref('')
  const isSending = ref(false)
  const isStreaming = ref(false)
  const playerState = ref<any | null>(null)
  const combatState = ref<any | null>(null)
  const debugMode = ref(false)

  let currentStreamingMessageId: string | null = null

  const addUserMessage = (content: string) => {
    messages.value.push(createMessage('user', content))
  }

  const addAssistantMessage = (content: string, isStreamingChunk: boolean = false) => {
    if (!content.trim() && !isStreamingChunk) return
    
    if (isStreamingChunk && currentStreamingMessageId) {
      const index = messages.value.findIndex(m => m.id === currentStreamingMessageId)
      if (index !== -1) {
        messages.value[index].content += content
        return
      }
    }
    
    const newMessage = createMessage('assistant', content, { type: 'text' })
    messages.value.push(newMessage)
    currentStreamingMessageId = newMessage.id
  }

  const addCombatMessage = (content: string, hpChanges: HpChange[]) => {
    if (content.trim() || hpChanges.length) {
      messages.value.push({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: content || '',
        timestamp: Date.now(),
        type: 'combat_action',
        metadata: { hp_changes: hpChanges },
        isHistory: false,
      })
    }
    currentStreamingMessageId = null
  }

  const addToolMessage = (content: string) => {
    messages.value.push({
      id: crypto.randomUUID(),
      role: 'assistant',
      content,
      timestamp: Date.now(),
      type: 'tool',
      isHistory: false,
    })
    currentStreamingMessageId = null
  }

  const addConfirmedMessage = (reason?: string) => {
    messages.value.push(createMessage('user', `[掷骰确认: ${reason || '无'}]`))
    currentStreamingMessageId = null
  }

  const setPendingAction = (action: PendingAction | null) => {
    pendingAction.value = action
  }

  const setError = (error: string) => {
    errorText.value = error
  }

  const setSending = (sending: boolean) => {
    isSending.value = sending
    if (sending) {
      isStreaming.value = true
      currentStreamingMessageId = null
    } else {
      isStreaming.value = false
      currentStreamingMessageId = null
    }
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

  const setMessages = (msgs: ChatMessage[]) => {
    messages.value = msgs.map((msg) => ({
      ...msg,
      id: msg.id || crypto.randomUUID(),
      timestamp: msg.timestamp ?? Date.now(),
      isHistory: true,   // 历史消息标记为 true
    }))
    currentStreamingMessageId = null
  }

  const toggleDebugMode = () => {
    debugMode.value = !debugMode.value
  }

  return {
    messages,
    pendingAction,
    errorText,
    isSending,
    isStreaming,
    playerState,
    combatState,
    debugMode,
    addUserMessage,
    addAssistantMessage,
    addCombatMessage,
    addToolMessage,
    addConfirmedMessage,
    setPendingAction,
    setError,
    setSending,
    clearError,
    setPlayerState,
    setCombatState,
    setMessages,
    toggleDebugMode,
  }
}