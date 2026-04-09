<!-- frontend/src/Pages_/ChatPage.vue -->
<template>
  <div class="chat-page" ref="containerRef">
    <!-- 左侧聊天区 -->
    <div class="chat-main" :class="{ hidden: rightWidth === 100 }">
      <!-- 聊天组件 -->
      <div class="chat-container">
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

    <!-- 拖拽条 -->
    <div 
      v-if="rightWidth > 0 && rightWidth < 100" 
      class="resize-handle" 
      @mousedown="startDrag"
    ></div>

    <!-- 右侧功能区 -->
    <div class="function-area" :style="{ width: rightWidth + '%' }">
      <CombatPanel :combat="combatState" />
    </div>

    <!-- 圆形控制按钮 -->
    <Transition name="fade">
      <button 
        v-if="showToggleBtn" 
        class="toggle-btn"
        :class="{ rotated: rightWidth === 0 }"
        @click="togglePanel"
      >
        {{ rightWidth === 0 ? '◀' : '▶' }}
      </button>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import ChatMessage from '../components/Chat/ChatMessage.vue'
import ChatInput from '../components/Chat/ChatInput.vue'
import ActionPanel from '../components/Chat/ActionPanel.vue'
import CombatPanel from '../components/Chat/CombatPanel.vue'
import { useChatSession } from '../composables/useChatSession'
import { useChatMessages } from '../composables/useChatMessages'
import { useChatSender } from '../composables/useChatSender'

// 导入样式
import '../styles_/chat-page.css'

// 右侧面板状态
const containerRef = ref<HTMLElement | null>(null)
const rightWidth = ref(25)
const showToggleBtn = ref(false)
const isDragging = ref(false)

// 聊天逻辑
const { sessionId, updateSessionId } = useChatSession()
const {
  messages,
  pendingAction,
  errorText,
  isSending,
  combatState,
  addUserMessage,
  addAssistantMessage,
  addConfirmedMessage,
  setPendingAction,
  setPlayerState,
  setCombatState,
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
  setPlayerState,
  setCombatState,
  setError,
  setSending,
  clearError,
  pendingAction
)

// 切换面板
const togglePanel = () => {
  if (rightWidth.value === 0) {
    rightWidth.value = 25
  } else {
    rightWidth.value = 0
  }
}

// 拖拽逻辑
const startDrag = (e: MouseEvent) => {
  if (!containerRef.value) return
  isDragging.value = true
  
  const container = containerRef.value
  const startX = e.clientX
  const startWidth = rightWidth.value
  const containerWidth = container.clientWidth

  const onMouseMove = (moveEvent: MouseEvent) => {
    if (!isDragging.value) return
    
    const deltaX = moveEvent.clientX - startX
    let newPercent = startWidth - (deltaX / containerWidth) * 100
    newPercent = Math.max(0, Math.min(100, newPercent))
    
    if (newPercent >= 80) {
      rightWidth.value = 100
      endDrag()
    } else {
      rightWidth.value = newPercent
    }
  }

  const onMouseUp = () => {
    endDrag()
  }

  const endDrag = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// 鼠标靠近右边缘显示按钮
const handleMouseMove = (e: MouseEvent) => {
  const windowWidth = window.innerWidth
  const distance = windowWidth - e.clientX
  showToggleBtn.value = distance < 50
}

onMounted(() => {
  document.addEventListener('mousemove', handleMouseMove)
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
})
</script>