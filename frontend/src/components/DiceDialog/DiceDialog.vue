<template>
  <Teleport to="body">
    <div v-if="visible" class="dialog-overlay" @click.self="close">
      <div class="dialog-content">
        <h3>🎲 掷骰子 (d20)</h3>

        <!-- 结果显示 -->
        <p v-if="result" class="result">结果：<strong>{{ result }}</strong></p>
        <p v-else class="result-placeholder">点击按钮掷骰</p>

        <!-- 按钮组 -->
        <div class="button-group">
          <button class="roll-btn" @click="roll" :disabled="isRolling">
            {{ isRolling ? '掷骰中...' : '掷骰' }}
          </button>
          <button class="close-btn" @click="close">关闭</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

// 状态
const result = ref<number | null>(null)
const isRolling = ref(false)

// 后端接口地址（预留）
const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// 调用后端掷骰接口
const rollDiceOnBackend = async (): Promise<number> => {
  // TODO: 后端接口准备好后，替换为真实调用
  // const response = await fetch(`${API_BASE}/dice/roll`, {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify({ diceType: 20, count: 1 })
  // })
  // const data = await response.json()
  // return data.total
  
  // 临时：前端模拟（等待后端接口）
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(Math.floor(Math.random() * 20) + 1)
    }, 300)
  })
}

// 掷骰
const roll = async () => {
  if (isRolling.value) return
  
  isRolling.value = true
  result.value = null
  
  try {
    const diceResult = await rollDiceOnBackend()
    result.value = diceResult
  } catch (error) {
    console.error('掷骰失败:', error)
    result.value = Math.floor(Math.random() * 20) + 1 // 降级方案
  } finally {
    isRolling.value = false
  }
}

const close = () => {
  emit('close')
  // 重置状态
  result.value = null
  isRolling.value = false
}
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog-content {
  background: rgba(26, 26, 30, 0.95);
  backdrop-filter: blur(20px);
  border: 0.5px solid rgba(255, 255, 255, 0.1);
  border-radius: 24px;
  padding: 32px;
  min-width: 280px;
  text-align: center;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
}

.dialog-content h3 {
  margin: 0 0 20px;
  color: #ffffff;
  font-size: 24px;
}

.result {
  font-size: 18px;
  margin: 24px 0;
  color: #e5e5ea;
}

.result strong {
  font-size: 48px;
  color: #42b883;
  margin-left: 12px;
}

.result-placeholder {
  font-size: 16px;
  margin: 24px 0;
  color: #6c6c70;
}

.button-group {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 8px;
}

.roll-btn {
  padding: 10px 28px;
  background: #42b883;
  border: none;
  border-radius: 40px;
  color: white;
  font-weight: 600;
  font-size: 16px;
  cursor: pointer;
  transition: all 0.2s;
}

.roll-btn:hover:not(:disabled) {
  background: #359f6b;
  transform: scale(1.02);
}

.roll-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.close-btn {
  padding: 10px 28px;
  background: rgba(255, 255, 255, 0.1);
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  border-radius: 40px;
  color: #e5e5ea;
  font-size: 16px;
  cursor: pointer;
  transition: all 0.2s;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}
</style>
