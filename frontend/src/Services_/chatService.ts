// frontend/src/Services_/chatService.ts
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp?: string | number
  avatar?: string
  displayName?: string
  // 扩展字段：消息子类型 + 元数据
  type?: 'text' | 'combat_action' | 'tool' | 'loading' 
  metadata?: { hp_changes?: HpChange[] }
 isHistory?: boolean   // 新增：标记是否为历史消息
}

export interface HpChange {
  id: string
  name: string
  old_hp: number
  new_hp: number
  max_hp: number
}

export interface AttackRoll {
  raw_roll: number
  attack_bonus: number
  final_total: number
  hit_total?: number
  target_ac: number
  attack_name?: string
}

export interface ReactionResponse {
  spell_id: string | null
  slot_level?: number | null
}

export type PendingAction = {
  type: string
  reason?: string
  formula?: string
  summary?: string
  hp_changes?: HpChange[]
  attack_roll?: AttackRoll
  [key: string]: any // allow arbitrary additional fields like available_reactions, attacker, etc.
}

export type ChatResponsePayload = {
  reply?: string
  plan?: string | null
  session_id?: string
  pending_action?: PendingAction | null
  player?: any
  combat?: any
}

// SSE 事件回调集
export interface SSECallbacks {
  onAssistantMessage?: (content: string) => void
  onCombatAction?: (content: string, hpChanges: HpChange[]) => void
  onToolMessage?: (content: string) => void
  onStateUpdate?: (player: any, combat: any) => void
  onPendingAction?: (action: PendingAction | null) => void
  onDiceRoll?: (rawRoll: number, finalTotal: number) => void | Promise<void>
  onDone?: (sessionId: string) => void
  onError?: (message: string) => void
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

// 手工解析 SSE 文本流
function parseSSEChunk(text: string): Array<{ event: string; data: string }> {
  const events: Array<{ event: string; data: string }> = []
  let currentEvent = ''
  let currentData = ''

  for (const line of text.split('\n')) {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      currentData = line.slice(6)
    } else if (line === '') {
      if (currentEvent && currentData) {
        events.push({ event: currentEvent, data: currentData })
      }
      currentEvent = ''
      currentData = ''
    }
  }
  return events
}

export const chatService = {
  // 保留原同步接口作为 fallback
  async sendMessage(params: {
    session_id: string | null
    message?: string
    resume_action?: string
    reaction_response?: ReactionResponse
  }): Promise<ChatResponsePayload> {
    const payload: any = { session_id: params.session_id }
    if (params.message !== undefined) payload.message = params.message
    if (params.resume_action !== undefined) payload.resume_action = params.resume_action
    if (params.reaction_response !== undefined) payload.reaction_response = params.reaction_response

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
  },

  // SSE 流式接口：POST + ReadableStream 手工解析
  async sendMessageStream(
    params: {
      session_id: string | null
      message?: string
      resume_action?: string
      reaction_response?: ReactionResponse
    },
    callbacks: SSECallbacks
  ): Promise<void> {
    const payload: any = { session_id: params.session_id }
    if (params.message !== undefined) payload.message = params.message
    if (params.resume_action !== undefined) payload.resume_action = params.resume_action
    if (params.reaction_response !== undefined) payload.reaction_response = params.reaction_response

    const response = await fetch('/api/chat/stream', {
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
      throw new ChatApiError(
        response.status,
        parsed.message || `请求失败（HTTP ${response.status}）`,
        parsed.code,
        parsed.requestId
      )
    }

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let diceAnimationQueue: Promise<void> = Promise.resolve()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const events = parseSSEChunk(buffer)

      // 保留未消费的不完整行
      const lastNewline = buffer.lastIndexOf('\n\n')
      buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 2) : buffer

      for (const { event, data } of events) {
        try {
          const parsed = JSON.parse(data)
          switch (event) {
            case 'assistant_message':
              callbacks.onAssistantMessage?.(parsed.content)
              break
            case 'combat_action':
              callbacks.onCombatAction?.(parsed.content, parsed.hp_changes || [])
              break
            case 'tool_message':
              callbacks.onToolMessage?.(parsed.content)
              break
            case 'dice_roll':
              if (callbacks.onDiceRoll) {
                diceAnimationQueue = diceAnimationQueue
                  .catch(() => undefined)
                  .then(() => Promise.resolve(callbacks.onDiceRoll?.(parsed.raw_roll, parsed.final_total)))
                  .catch((error) => {
                    console.error('Dice animation failed:', error)
                  })
              }
              break
            case 'state_update':
              callbacks.onStateUpdate?.(parsed.player, parsed.combat)
              break
            case 'pending_action':
              callbacks.onPendingAction?.(parsed ?? null)
              break
            case 'done':
              callbacks.onDone?.(parsed.session_id)
              break
            case 'error':
              callbacks.onError?.(parsed.message)
              break
          }
        } catch {
          // 解析失败则跳过
        }
      }
    }
  },

  // 历史消息接口
  async fetchHistory(sessionId: string, limit: number = 10): Promise<{
    messages: Array<{ role: string; content: string }>
    player: any
    combat: any
  }> {
    const response = await fetch(
      `/api/chat/history?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`
    )
    if (!response.ok) {
      return { messages: [], player: null, combat: null }
    }
    return await response.json()
  }
}
