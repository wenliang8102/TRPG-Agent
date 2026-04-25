// frontend/src/composables/useChatSender.ts
import { ChatApiError, chatService } from '../Services_/chatService'
import type { HpChange, ReactionResponse } from '../Services_/chatService'
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
  addConfirmedMessage: (reason?: string) => void,
  setPendingAction: (action: any) => void,
  setPlayerState: (state: any) => void,
  setCombatState: (state: any) => void,
  setError: (error: string) => void,
  setSending: (sending: boolean) => void,
  clearError: () => void,
  pendingActionRef: Ref<any>,
  onDiceRollAnimation?: (rawRoll: number) => Promise<void>,
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
        onDiceRoll: async (rawRoll, finalTotal) => {
          if (onDiceRollAnimation) {
            await onDiceRollAnimation(rawRoll)
          }
        },
        onStateUpdate: (player, combat) => {
          if (player !== undefined) setPlayerState(player)
          if (combat !== undefined) setCombatState(combat)
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

  const sendTextMessage = async (text: string) => {
    if (!text.trim()) return
    addUserMessage(text)
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