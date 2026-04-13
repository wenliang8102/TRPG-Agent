import { ChatApiError, chatService } from '../Services_/chatService'
import type { HpChange } from '../Services_/chatService'
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
  addAssistantMessage: (content: string) => void,
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
  onDiceRollAnimation?: (rawRoll: number) => Promise<void>
) {
  // SSE 流式发送的通用逻辑
  const streamRequest = async (params: {
    session_id: string | null
    message?: string
    resume_action?: string
  }) => {
    clearError()
    setSending(true)

    try {
      await chatService.sendMessageStream(params, {
        onAssistantMessage: (content) => addAssistantMessage(content),
        onCombatAction: (content, hpChanges) => addCombatMessage(content, hpChanges),
        onToolMessage: (content) => addToolMessage(content),
        onDiceRoll: async (rawRoll) => {
          if (onDiceRollAnimation) {
            await onDiceRollAnimation(rawRoll)
          }
        },
        onStateUpdate: (player, combat) => {
          if (player !== undefined) setPlayerState(player)
          if (combat !== undefined) setCombatState(combat)
        },
        onPendingAction: (action) => setPendingAction(action),
        onDone: (sid) => {
          if (sid) updateSessionId(sid)
        },
        onError: (msg) => setError(msg),
      })
    } catch (error) {
      setError(buildUserError(error))
      console.error(error)
    } finally {
      setSending(false)
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

  // 玩家死亡后的恢复选择
  const respondToPlayerDeath = async (choice: 'revive' | 'end') => {
    setPendingAction(null)
    await streamRequest({ session_id: sessionId.value, resume_action: choice })
  }

  return { sendTextMessage, confirmDiceRoll, respondToPlayerDeath }
}