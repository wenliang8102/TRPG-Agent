// frontend/src/Services_/chatService.ts
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string | number
  avatar?: string      // 头像 URL（可选）
  displayName?: string // 显示名称（可选）
}

export type PendingAction = {
  type: string
  reason?: string
  formula?: string
}

export type ChatResponsePayload = {
  reply?: string
  plan?: string | null
  session_id?: string
  pending_action?: PendingAction | null
  player?: any
  combat?: any
}

export class ChatApiError extends Error {
  status: number
  code?: string
  requestId?: string

  constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message)
    this.name = 'ChatApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

const parseErrorPayload = (payload: any): { message: string; code?: string; requestId?: string } => {
  if (!payload) return { message: '' }

  const detail = payload.detail ?? payload
  if (typeof detail === 'string') {
    return { message: detail }
  }

  if (typeof detail === 'object') {
    return {
      message: String(detail.message ?? ''),
      code: typeof detail.code === 'string' ? detail.code : undefined,
      requestId: typeof detail.request_id === 'string' ? detail.request_id : undefined
    }
  }

  return { message: '' }
}

export const chatService = {
  async sendMessage(params: {
    session_id: string | null
    message?: string
    resume_action?: string
  }): Promise<ChatResponsePayload> {
    const payload: any = { session_id: params.session_id }
    if (params.message !== undefined) payload.message = params.message
    if (params.resume_action !== undefined) payload.resume_action = params.resume_action

    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })

    if (!response.ok) {
      let parsed: { message: string; code?: string; requestId?: string } = { message: '' }
      try {
        const body = await response.json()
        parsed = parseErrorPayload(body)
      } catch {
        parsed = { message: '' }
      }

      const fallbackMessage = `请求失败（HTTP ${response.status}）`
      throw new ChatApiError(
        response.status,
        parsed.message || fallbackMessage,
        parsed.code,
        parsed.requestId
      )
    }

    return await response.json()
  }
}

