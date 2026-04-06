<template>
  <div class="chat-container">
    <div class="chat-panel">
      <header class="chat-header">
        <h1>TRPG 助手</h1>
      </header>

      <div class="message-list">
        <ChatMessage
          v-for="(msg, idx) in messages"
          :key="idx"
          :message="msg"
        />
      </div>

      <p v-if="errorText" class="error-text">{{ errorText }}</p>

      <ActionPanel
        :pending-action="pendingAction"
        :disabled="isSending"
        @confirm="confirmDiceRoll"
      />

      <ChatInput
        :disabled="isSending || pendingAction !== null"
        button-text="发送"
        placeholder="输入内容并回车发送..."
        @send="sendTextMessage"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import ActionPanel from './ActionPanel.vue'
import { useChatSession } from '../../composables/useChatSession'
import { useChatMessages } from '../../composables/useChatMessages'
import { useChatSender } from '../../composables/useChatSender'

const { sessionId, updateSessionId } = useChatSession()
const {
  messages,
  pendingAction,
  errorText,
  isSending,
  addUserMessage,
  addAssistantMessage,
  addConfirmedMessage,
  setPendingAction,
  setError,
  setSending,
  clearError
} = useChatMessages()

const { sendTextMessage, confirmDiceRoll } = useChatSender(
  sessionId,
  updateSessionId,
  addUserMessage,
  addAssistantMessage,
  addConfirmedMessage,
  setPendingAction,
  setError,
  setSending,
  clearError,
  pendingAction
)
</script>

<style scoped>
.chat-container {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  box-sizing: border-box;
}
.chat-panel {
  width: 100%;
  max-width: 780px;
  height: 100%;
  border: 1px solid #2f2f2f;
  border-radius: 12px;
  padding: 16px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.chat-header h1 {
  margin: 0 0 12px;
  font-size: 22px;
}
.message-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 58vh;
  overflow-y: auto;
  padding: 8px 4px;
}
.error-text {
  color: #ff6b6b;
  margin-top: 10px;
}
</style>