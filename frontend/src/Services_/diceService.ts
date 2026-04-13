// frontend/src/Services_/diceService.ts

export interface RollDiceRequest {
  diceType: number  // 骰子面数，如 20
  count: number     // 骰子数量，如 1
  modifier: number  // 加值，如 0
}

export interface RollDiceResponse {
  success: boolean
  data: {
    results: number[]   // 每次掷骰结果，如 [15]
    total: number       // 总和 + 加值
    timestamp: string   // ISO 时间戳
  }
}

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8080/api'

/**
 * 后端掷骰接口
 */
export async function rollDice(request: RollDiceRequest): Promise<RollDiceResponse> {
  // TODO: 后端接口准备好后，替换为真实调用
  // const response = await fetch(`${API_BASE}/dice/roll`, {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify(request)
  // })
  // return response.json()
  
  // 临时：前端模拟（等待后端接口）
  return new Promise((resolve) => {
    setTimeout(() => {
      const results: number[] = []
      for (let i = 0; i < request.count; i++) {
        results.push(Math.floor(Math.random() * request.diceType) + 1)
      }
      const total = results.reduce((a, b) => a + b, 0) + request.modifier
      resolve({
        success: true,
        data: {
          results,
          total,
          timestamp: new Date().toISOString()
        }
      })
    }, 300)
  })
}