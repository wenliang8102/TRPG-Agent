<script setup lang="ts">
import { ref } from 'vue'

type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

const inputText = ref('')
const isSending = ref(false)
const errorText = ref('')
const messages = ref<ChatMessage[]>([
  { role: 'assistant', content: '你好，我是 TRPG 助手。你可以直接开始提问。' }
])

const sendMessage = async () => {
  const text = inputText.value.trim()
  if (!text || isSending.value) return

  errorText.value = ''
  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  isSending.value = true

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const data = await response.json()
    const reply = String(data.reply ?? '').trim() || '模型没有返回内容。'
    messages.value.push({ role: 'assistant', content: reply })
  } catch (error) {
    errorText.value = '发送失败，请检查后端服务和模型配置。'
    console.error(error)
  } finally {
    isSending.value = false
  }
}
</script>

<template>
  <main class="chat-page">
    <section class="chat-panel">
      <header class="chat-header">
        <h1>TRPG 对话测试</h1>
      </header>

      <div class="message-list">
        <article
          v-for="(message, index) in messages"
          :key="index"
          :class="['message-item', message.role]"
        >
          <p class="message-role">{{ message.role === 'user' ? '你' : 'AI' }}</p>
          <p class="message-content">{{ message.content }}</p>
        </article>
      </div>

      <p v-if="errorText" class="error-text">{{ errorText }}</p>

      <form class="input-row" @submit.prevent="sendMessage">
        <input
          v-model="inputText"
          type="text"
          placeholder="输入内容并回车发送..."
          :disabled="isSending"
        />
        <button type="submit" :disabled="isSending || !inputText.trim()">
          {{ isSending ? '发送中...' : '发送' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.chat-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  box-sizing: border-box;
}

.chat-panel {
  width: 100%;
  max-width: 780px;
  border: 1px solid #2f2f2f;
  border-radius: 12px;
  padding: 16px;
  box-sizing: border-box;
}

.chat-header h1 {
  margin: 0 0 12px;
  font-size: 22px;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 58vh;
  overflow-y: auto;
  padding: 8px 4px;
}

.message-item {
  border-radius: 10px;
  padding: 10px 12px;
}

.message-item.user {
  background: rgba(66, 184, 131, 0.2);
}

.message-item.assistant {
  background: rgba(140, 140, 255, 0.15);
}

.message-role {
  font-size: 12px;
  opacity: 0.8;
  margin-bottom: 4px;
}

.message-content {
  white-space: pre-wrap;
  line-height: 1.5;
}

.input-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.input-row input {
  flex: 1;
  height: 40px;
  border-radius: 8px;
  border: 1px solid #3f3f3f;
  padding: 0 12px;
}

.input-row button {
  min-width: 96px;
  border: none;
  border-radius: 8px;
  background: #42b883;
  color: #fff;
  cursor: pointer;
}

.input-row button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-text {
  color: #ff6b6b;
  margin-top: 10px;
}
</style>