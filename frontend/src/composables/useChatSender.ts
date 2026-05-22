// frontend/src/composables/useChatSender.ts
import { ChatApiError, chatService } from '../Services_/chatService'
import type { HpChange, ReactionResponse, DiceRollEvent } from '../Services_/chatService'
import type { Ref } from 'vue'

const buildUserError = (error: unknown): string => {
  if (error instanceof ChatApiError) {
    const requestHint = error.requestId ? ` (request_id: ${error.requestId})` : ''
    return `${error.message}${requestHint}`
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '发送失败，请检查后端服务和模型配置。'
}

export function useChatSender(
  sessionId: Ref<string | null>,
  updateSessionId: (id: string) => void,
  addUserMessage: (content: string) => void,
  addAssistantMessage: (content: string, isStreamingChunk?: boolean) => void,
  addCombatMessage: (content: string, hpChanges: HpChange[]) => void,
  addToolMessage: (content: string) => void,
  addDiceRollMessage: (roll: DiceRollEvent) => void,
  addConfirmedMessage: (reason?: string) => void,
  setPendingAction: (action: any) => void,
  setPlayerState: (state: any) => void,
  setCombatState: (state: any) => void,
  setSpaceState: (state: any) => void,
  setSceneUnitsState: (state: Record<string, any> | null) => void,
  setDeadUnitsState: (state: Record<string, any> | null) => void,
  setError: (error: string) => void,
  setSending: (sending: boolean) => void,
  clearError: () => void,
  pendingActionRef: Ref<any>,
  startLoading?: () => void,
  stopLoading?: () => void
) {
  const streamRequest = async (params: {
    session_id: string | null
    message?: string
    resume_action?: string
    reaction_response?: ReactionResponse
  }) => {
    clearError()
    setSending(true)

    if (startLoading) startLoading()

    let loadingStopped = false
    let assistantLoadingStopped = false   // 新增：标记文本消息是否已停止 loading
    const stopLoadingOnce = () => {
      if (!loadingStopped && stopLoading) {
        loadingStopped = true
        stopLoading()
      }
    }

    try {
      await chatService.sendMessageStream(params, {
        onAssistantMessage: (content) => {
          // 第一次收到文本内容时立即停止 loading
          if (!assistantLoadingStopped) {
            assistantLoadingStopped = true
            stopLoadingOnce()
          }
          addAssistantMessage(content, true)
        },
        onCombatAction: (content, hpChanges) => {
          stopLoadingOnce() // 战斗消息立即停止 loading
          addCombatMessage(content, hpChanges)
        },
        onToolMessage: (content) => {
          stopLoadingOnce() // 工具消息立即停止 loading
          addToolMessage(content)
        },
        onDiceRoll: async (roll) => {
          stopLoadingOnce()
          addDiceRollMessage(roll)
        },
        onStateUpdate: (player, combat, _sceneUnits, _deadUnits, space) => {
          if (player !== undefined) setPlayerState(player)
          if (combat !== undefined) setCombatState(combat)
          if (_sceneUnits !== undefined) setSceneUnitsState(_sceneUnits)
          if (_deadUnits !== undefined) setDeadUnitsState(_deadUnits)
          if (space !== undefined) setSpaceState(space)
        },
        onPendingAction: (action) => {
          stopLoadingOnce()
          setPendingAction(action)
        },
        onDone: (sid) => {
          stopLoadingOnce() // 兜底：防止 loading 一直显示（如空消息时）
          if (sid) updateSessionId(sid)
          setSending(false)
        },
        onError: (msg) => {
          stopLoadingOnce()
          setError(msg)
          setSending(false)
        },
      })
    } catch (error) {
      stopLoadingOnce()
      setError(buildUserError(error))
      setSending(false)
      console.error(error)
    }
  }

  const sendTextMessage = async (text: string, options?: { silent?: boolean }) => {
    if (!text.trim()) return
    if (!options?.silent) {
      addUserMessage(text)
    }
    await streamRequest({ session_id: sessionId.value, message: text })
  }

  const confirmDiceRoll = async () => {
    if (!pendingActionRef.value) return
    addConfirmedMessage(pendingActionRef.value.reason)
    setPendingAction(null)
    await streamRequest({ session_id: sessionId.value, resume_action: 'confirmed' })
  }

  const respondToPlayerDeath = async (choice: 'revive' | 'end') => {
    setPendingAction(null)
    await streamRequest({ session_id: sessionId.value, resume_action: choice })
  }

  const respondToReaction = async (choice: { spell_id: string; slot_level: number } | null) => {
    const payload = choice ?? { spell_id: null }
    await streamRequest({ session_id: sessionId.value, reaction_response: payload })
  }

  return { sendTextMessage, confirmDiceRoll, respondToPlayerDeath, respondToReaction }
}
