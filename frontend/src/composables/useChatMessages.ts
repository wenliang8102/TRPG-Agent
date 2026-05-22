// frontend/src/composables/useChatMessages.ts
import { ref } from 'vue'
import type { ChatMessage, PendingAction, HpChange, DiceRollEvent } from '../Services_/chatService'

const createMessage = (
  role: ChatMessage['role'],
  content: string,
  extras: Partial<ChatMessage> = {}
): ChatMessage => ({
  id: crypto.randomUUID(),
  role,
  content,
  timestamp: Date.now(),
  isHistory: false,
  ...extras,
})

export const WELCOME_MESSAGE = '你好，我是奥秘之桌。你可以直接开始提问。'

const createWelcomeMessage = () => createMessage('assistant', WELCOME_MESSAGE)

const HIDDEN_SYSTEM_MESSAGE_PREFIXES = [
  '【探索状态移动请求】',
  '【战斗状态移动请求】',
  '【战术移动请求】',
]

// 中文注释：地图移动请求本质是发给后端的结构化指令，不应污染用户可见聊天历史。
const isHiddenSystemMessage = (content: string) => HIDDEN_SYSTEM_MESSAGE_PREFIXES.some(prefix => content.startsWith(prefix))

const normalizeCombatState = (state: any) => {
  if (!state || typeof state !== 'object') return null

  const currentActorId = typeof state.current_actor_id === 'string' ? state.current_actor_id.trim() : ''
  const participants = state.participants
  const hasParticipants = !!participants && typeof participants === 'object' && Object.keys(participants).length > 0

  // 这里不推断业务胜负，只把后端已经表现为空壳的 combat 快照归一化为 null，保证前后端状态口径一致
  if (!currentActorId && !hasParticipants) return null
  return state
}

export function useChatMessages(initialDebugMode: boolean = false) {
  const messages = ref<ChatMessage[]>([
    createWelcomeMessage()
  ])
  const pendingAction = ref<PendingAction | null>(null)
  const errorText = ref('')
  const isSending = ref(false)
  const isStreaming = ref(false)
  const playerState = ref<any | null>(null)
  const combatState = ref<any | null>(null)
  const spaceState = ref<any | null>(null)
  const sceneUnitsState = ref<Record<string, any> | null>(null)
  const deadUnitsState = ref<Record<string, any> | null>(null)
  const debugMode = ref(initialDebugMode)

  let currentStreamingMessageId: string | null = null
  let loadingMessageId: string | null = null

  // ========== Loading 管理 ==========
  const startLoading = () => {
    // 先移除已有的 loading 消息
    stopLoading()
    const loadingMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      type: 'loading',
      isHistory: false,
    }
    messages.value.push(loadingMsg)
    loadingMessageId = loadingMsg.id
  }

  const stopLoading = () => {
    if (loadingMessageId) {
      const index = messages.value.findIndex(m => m.id === loadingMessageId)
      if (index !== -1) {
        messages.value.splice(index, 1)
      }
      loadingMessageId = null
    }
  }

  // ========== 消息添加 ==========
  const addUserMessage = (content: string) => {
    stopLoading()  // 用户发送新消息时清除可能残留的 loading
    messages.value.push(createMessage('user', content))
  }

  const addAssistantMessage = (content: string, isStreamingChunk: boolean = false) => {
 
    if (!content.trim()) return
    
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

  const addDiceRollMessage = (roll: DiceRollEvent) => {
    stopLoading()
    messages.value.push(createMessage('assistant', '', {
      type: 'dice_roll',
      metadata: { dice_roll: roll },
    }))
    currentStreamingMessageId = null
  }

  const addConfirmedMessage = (reason?: string) => {
    messages.value.push(createMessage('user', `[掷骰确认: ${reason || '无'}]`))
    currentStreamingMessageId = null
  }

  // ========== 状态设置 ==========
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
    combatState.value = normalizeCombatState(state)
  }

  const setSpaceState = (state: any) => {
    spaceState.value = state
  }

  const setSceneUnitsState = (state: Record<string, any> | null) => {
    sceneUnitsState.value = state
  }

  const setDeadUnitsState = (state: Record<string, any> | null) => {
    deadUnitsState.value = state
  }

  const setMessages = (msgs: ChatMessage[]) => {
    stopLoading()  // 清除 loading
    messages.value = msgs
      .filter(msg => !isHiddenSystemMessage(msg.content))
      .map((msg) => ({
      ...msg,
      id: msg.id || crypto.randomUUID(),
      timestamp: msg.timestamp ?? Date.now(),
      isHistory: true,
      }))
    currentStreamingMessageId = null
  }

  const resetChatState = () => {
    stopLoading()
    messages.value = [createWelcomeMessage()]
    pendingAction.value = null
    errorText.value = ''
    isSending.value = false
    isStreaming.value = false
    playerState.value = null
    combatState.value = null
    spaceState.value = null
    sceneUnitsState.value = null
    deadUnitsState.value = null
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
    spaceState,
    sceneUnitsState,
    deadUnitsState,
    debugMode,
    addUserMessage,
    addAssistantMessage,
    addCombatMessage,
    addToolMessage,
    addDiceRollMessage,
    addConfirmedMessage,
    setPendingAction,
    setError,
    setSending,
    clearError,
    setPlayerState,
    setCombatState,
    setSpaceState,
    setSceneUnitsState,
    setDeadUnitsState,
    setMessages,
    resetChatState,
    toggleDebugMode,
    startLoading,
    stopLoading,
  }
}
