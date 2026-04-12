<!-- frontend/src/Pages_/ChatPage.vue -->
<template>
  <div class="chat-page" ref="containerRef">
    <!-- 左侧聊天区 -->
    <div class="chat-main" :class="{ hidden: rightWidth === 100 }">
      <div class="chat-container">
        <header class="chat-header">
          <h1>TRPG 助手</h1>
          <div class="header-actions">
            <button
              class="debug-toggle"
              :class="{ active: debugMode }"
              @click="toggleDebugMode"
              title="调试模式"
            >
              🔧
            </button>
          </div>
        </header>

        <div class="message-list" ref="messageListRef">
          <ChatMessage
            v-for="msg in messages"
            :key="msg.id"
            :message="msg"
          />
        </div>

        <p v-if="errorText" class="error-text">{{ errorText }}</p>

        <ActionPanel
          :pending-action="pendingAction"
          :disabled="isSending"
          @confirm="confirmDiceRoll"
          @revive="respondToPlayerDeath('revive')"
          @end-combat="respondToPlayerDeath('end')"
        />

        <div v-if="showNextTurnBtn" class="next-turn-bar">
          <button
            class="next-turn-btn"
            :disabled="isSending"
            @click="sendTextMessage('我结束回合')"
          >
            结束回合 →
          </button>
        </div>

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

    <!-- 右侧功能区：动态切换组件 -->
    <div class="function-area" :style="{ width: rightWidth + '%' }">
      <component 
        :is="rightPanelComponent" 
        :combat="combatState"
        :player="playerState"
      />
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
import { ref, computed, provide, onMounted, onUnmounted, watch, nextTick } from 'vue'
import ChatMessage from '../components/Chat/ChatMessage.vue'
import ChatInput from '../components/Chat/ChatInput.vue'
import ActionPanel from '../components/Chat/ActionPanel.vue'
import CombatPanel from '../components/Chat/CombatPanel.vue'
import CharacterPanel from '../components/Chat/CharacterPanel.vue'  // 新增
import { useChatSession } from '../composables/useChatSession'
import { useChatMessages } from '../composables/useChatMessages'
import { useChatSender } from '../composables/useChatSender'
import { chatService } from '../Services_/chatService'

import '../styles_/chat-page.css'

// 右侧面板状态
const containerRef = ref<HTMLElement | null>(null)
const messageListRef = ref<HTMLElement | null>(null)
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
  playerState,        // 新增：获取玩家状态
  debugMode,
  addUserMessage,
  addAssistantMessage,
  addCombatMessage,
  addToolMessage,
  addConfirmedMessage,
  setPendingAction,
  setPlayerState,
  setCombatState,
  setError,
  setSending,
  clearError,
  setMessages,
  toggleDebugMode,
} = useChatMessages()

// 动态右侧组件：有战斗时显示 CombatPanel，否则显示 CharacterPanel
const rightPanelComponent = computed(() => {
  return combatState.value ? CombatPanel : CharacterPanel
})

// 通过 provide 向子组件注入 debugMode
provide('debugMode', debugMode)

const { sendTextMessage, confirmDiceRoll, respondToPlayerDeath } = useChatSender(
  sessionId,
  updateSessionId,
  addUserMessage,
  addAssistantMessage,
  addCombatMessage,
  addToolMessage,
  addConfirmedMessage,
  setPendingAction,
  setPlayerState,
  setCombatState,
  setError,
  setSending,
  clearError,
  pendingAction
)

// 下一回合按钮：战斗中、玩家回合、无挂起动作
const showNextTurnBtn = computed(() => {
  if (!combatState.value || pendingAction.value) return false
  const currentActorId: string = combatState.value.current_actor_id || ''
  return currentActorId.startsWith('player_')
})

// 消息自动滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}
watch(messages, scrollToBottom, { deep: true })

// 初始化：加载历史消息
onMounted(async () => {
  document.addEventListener('mousemove', handleMouseMove)
  if (sessionId.value) {
    try {
      const history = await chatService.fetchHistory(sessionId.value)
      const shouldHydrateMessages = messages.value.length === 1
        && messages.value[0]?.role === 'assistant'
        && messages.value[0]?.content === '你好，我是 TRPG 助手。你可以直接开始提问。'

      if (history.messages.length > 0 && shouldHydrateMessages) {
        setMessages(history.messages.map(m => ({
          id: crypto.randomUUID(),
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: Date.now(),
        })))
      }
      if (history.player) setPlayerState(history.player)
      if (history.combat) setCombatState(history.combat)
    } catch {
      // 无历史则使用默认欢迎消息
    }
  }
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
})

// 切换面板
const togglePanel = () => {
  rightWidth.value = rightWidth.value === 0 ? 25 : 0
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
</script>

<style scoped>
/* 原有样式保持不变 */
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.debug-toggle {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 14px;
  cursor: pointer;
  opacity: 0.5;
  transition: all 0.2s;
}
.debug-toggle.active {
  opacity: 1;
  border-color: #a78bfa;
  background: rgba(139, 92, 246, 0.15);
}
.debug-toggle:hover {
  opacity: 0.8;
}

.next-turn-bar {
  padding: 8px 16px;
  text-align: center;
}
.next-turn-btn {
  padding: 8px 28px;
  background: #42b883;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.next-turn-btn:hover:not(:disabled) {
  background: #38a373;
}
.next-turn-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>