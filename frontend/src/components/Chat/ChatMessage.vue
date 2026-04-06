<template>
  <article :class="['message-item', message.role]">
    <p class="message-role">{{ message.role === 'user' ? '你' : 'AI' }}</p>
    <p class="message-content">{{ message.content }}</p>
  </article>
</template>

<script setup lang="ts">
import type { ChatMessage } from '../../Services_/chatService'

defineProps<{
  message: ChatMessage
}>()
</script>

<style scoped>
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
</style>