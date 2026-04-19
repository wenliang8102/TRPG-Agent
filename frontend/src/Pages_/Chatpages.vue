<!-- frontend/src/Pages_/ChatPage.vue -->
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

        <div class="message-list" ref="messageListRef" @scroll="handleScroll">
          <ChatMessage
            v-for="(msg, index) in messages"
            :key="msg.id"
            :message="msg"
            :is-streaming="isStreaming && index === messages.length - 1 && msg.role === 'assistant'"
            :scroll-to-bottom="scrollToBottom"
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

    <!-- 右侧功能区：始终显示 CharacterPanel -->
    <div class="function-area" :style="{ width: rightWidth + '%' }">
      <CharacterPanel
        ref="characterPanelRef"
        :external-player="playerState"
        :combat="combatState"
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

    <Dice3D v-if="showDiceAnimation" ref="dice3dRef" class="chat-dice-overlay" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, provide, onMounted, onUnmounted, watch, nextTick } from 'vue'
import ChatMessage from '../components/Chat/ChatMessage.vue'
import ChatInput from '../components/Chat/ChatInput.vue'
import ActionPanel from '../components/Chat/ActionPanel.vue'
import CharacterPanel from '../components/Chat/CharacterPanel.vue'
import Dice3D from '../components/Dice3D/Dice3D.vue'
import { useChatSession } from '../composables/useChatSession'
import { useChatMessages } from '../composables/useChatMessages'
import { useChatSender } from '../composables/useChatSender'
import { chatService } from '../Services_/chatService'

import '../styles_/Chatpages.css'

// 右侧面板状态
const containerRef = ref<HTMLElement | null>(null)
const messageListRef = ref<HTMLElement | null>(null)
const dice3dRef = ref<InstanceType<typeof Dice3D> | null>(null)
const characterPanelRef = ref<InstanceType<typeof CharacterPanel> | null>(null)
const showDiceAnimation = ref(false)
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
  playerState,
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
  isStreaming,
} = useChatMessages()

// 通过 provide 向子组件注入 debugMode
provide('debugMode', debugMode)

// 监听战斗状态变化，自动切换 CharacterPanel 视图
watch(combatState, (hasCombat) => {
  if (characterPanelRef.value) {
    if (hasCombat) {
      // 战斗开始/进行中 → 切换到血条视图
      characterPanelRef.value.setViewMode('hp')
    } else {
      // 战斗结束 → 切换回角色详情视图
      characterPanelRef.value.setViewMode('character')
    }
  }
}, { immediate: true })

const handleDiceRollAnim = async (rawRoll: number) => {
  showDiceAnimation.value = true
  await nextTick()
  if (dice3dRef.value) {
    await dice3dRef.value.throwDice(rawRoll)
    await new Promise((resolve) => setTimeout(resolve, 1500))
  }
  showDiceAnimation.value = false
}

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
  pendingAction,
  handleDiceRollAnim
)

const showNextTurnBtn = computed(() => {
  if (!combatState.value || pendingAction.value) return false
  const currentActorId: string = combatState.value.current_actor_id || ''
  return currentActorId.startsWith('player_')
})

const autoScrollDisabled = ref(false)

const isNearBottom = (): boolean => {
  const el = messageListRef.value
  if (!el) return true
  const threshold = 50
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

const handleScroll = () => {
  autoScrollDisabled.value = !isNearBottom()
}

const scrollToBottom = () => {
  nextTick(() => {
    if (!autoScrollDisabled.value && messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

watch(messages, scrollToBottom, { deep: true })

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
      // 忽略错误
    }
  }
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
})

const togglePanel = () => {
  rightWidth.value = rightWidth.value === 0 ? 25 : 0
}

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

  const onMouseUp = () => endDrag()

  const endDrag = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const handleMouseMove = (e: MouseEvent) => {
  const windowWidth = window.innerWidth
  const distance = windowWidth - e.clientX
  showToggleBtn.value = distance < 50
}
</script>