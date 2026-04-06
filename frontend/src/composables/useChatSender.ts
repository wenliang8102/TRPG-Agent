import { chatService } from '../Services_/chatService'
import type { Ref } from 'vue'

export function useChatSender(
  sessionId: Ref<string | null>,
  updateSessionId: (id: string) => void,
  addUserMessage: (content: string) => void,
  addAssistantMessage: (content: string) => void,
  addConfirmedMessage: (reason?: string) => void,
  setPendingAction: (action: any) => void,
  setError: (error: string) => void,
  setSending: (sending: boolean) => void,
  clearError: () => void,
  pendingActionRef: Ref<any>
) {
  const sendTextMessage = async (text: string) => {
    if (!text.trim()) return
    clearError()
    setSending(true)
    addUserMessage(text)

    try {
      const data = await chatService.sendMessage({
        session_id: sessionId.value,
        message: text
      })

      if (data.session_id) updateSessionId(data.session_id)
      setPendingAction(data.pending_action || null)

      const reply = String(data.reply ?? '').trim()
      if (reply) {
        addAssistantMessage(reply)
      } else if (!data.pending_action) {
        addAssistantMessage('模型没有返回内容。')
      }
    } catch (error) {
      setError('发送失败，请检查后端服务和模型配置。')
      console.error(error)
    } finally {
      setSending(false)
    }
  }

  const confirmDiceRoll = async () => {
    if (!pendingActionRef.value) return
    clearError()
    setSending(true)
    addConfirmedMessage(pendingActionRef.value.reason)

    try {
      const data = await chatService.sendMessage({
        session_id: sessionId.value,
        resume_action: 'confirmed'
      })

      if (data.session_id) updateSessionId(data.session_id)
      setPendingAction(data.pending_action || null)

      const reply = String(data.reply ?? '').trim()
      if (reply) {
        addAssistantMessage(reply)
      }
    } catch (error) {
      setError('发送失败，请检查后端服务和模型配置。')
      console.error(error)
    } finally {
      setSending(false)
    }
  }

  return { sendTextMessage, confirmDiceRoll }
}