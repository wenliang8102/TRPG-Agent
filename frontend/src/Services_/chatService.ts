export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
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

    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return await response.json()
  }
}