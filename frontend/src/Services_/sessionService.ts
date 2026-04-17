// frontend/src/Services_/sessionService.ts

export interface ChatSession {
  id: string
  title: string
  createdAt: number
  lastMessageAt: number
  preview?: string
  messageCount: number
}

// 临时模拟数据（待后端实现后替换）
export async function listSessions(): Promise<ChatSession[]> {
  // TODO: 调用后端 GET /api/sessions
  // 暂时返回模拟数据便于测试 UI
  return [
    {
      id: 'session-1',
      title: '石溪镇的冒险',
      createdAt: Date.now() - 86400000 * 2,
      lastMessageAt: Date.now() - 3600000,
      preview: '你击败了地精，获得了 25 金币...',
      messageCount: 24
    },
    {
      id: 'session-2',
      title: '黑暗森林探索',
      createdAt: Date.now() - 86400000 * 5,
      lastMessageAt: Date.now() - 86400000 * 2,
      preview: '你发现了一座古老的遗迹...',
      messageCount: 18
    }
  ]
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  // TODO: 调用后端 DELETE /api/sessions/:id
  console.log('删除会话:', sessionId)
  return true
}