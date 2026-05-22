<!-- frontend/src/Pages_/ChatPage.vue -->
<template>
  <div class="chat-page" ref="containerRef">
    <!-- 左侧聊天区 -->
    <div
      class="chat-main"
      :class="{ hidden: rightWidth === 100 }"
      :style="{ '--chat-font-scale': String(appSettings.fontScale) }"
    >
      <div class="chat-container">
        <header class="chat-header">
          <h1>奥秘之桌</h1>
          <div class="header-actions">
            <button
              class="session-action-btn"
              :disabled="isSending"
              @click="startNewSession"
              title="创建新会话"
            >
              <Plus :size="16" />
            </button>
            <button
              class="session-action-btn danger"
              :disabled="isSending || !sessionId"
              @click="deleteCurrentSession"
              title="删除当前会话"
            >
              <Trash2 :size="16" />
            </button>
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

        <ActionAvailabilityNotice
          :reasons="actionAvailabilityReasons"
          :text="actionAvailabilityText"
        />

        <div class="message-list" ref="messageListRef" @scroll="handleScroll">
          <ChatMessage
            v-for="msg in messages"
            :key="msg.id"
            :message="msg"
            :scroll-to-bottom="scrollToBottom"
            @firstChar="stopLoading"
          />
        </div>

        <p v-if="errorText" class="error-text">{{ errorText }}</p>

        <ActionPanel
          :pending-action="pendingAction"
          :disabled="isSending"
          @confirm="confirmDiceRoll"
          @revive="respondToPlayerDeath('revive')"
          @end-combat="respondToPlayerDeath('end')"
          @react="respondToReaction"
          @skip-reaction="respondToReaction(null)"
        />

        <div v-if="showEndTurnBar" class="end-turn-bar">
          <div class="end-turn-copy">
            <span class="end-turn-label">当前行动</span>
            <strong>{{ currentControlledActorName }}</strong>
          </div>
          <button
            class="end-turn-direct-btn"
            :disabled="isSending"
            @click="endCurrentControlledTurn"
            title="直接结束当前玩家/友方回合"
          >
            结束回合
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

    <!-- 右侧功能区 -->
    <div class="function-area" :style="{ width: rightWidth + '%' }">
      <CharacterSidebar
        ref="characterSidebarRef"
        :external-player="playerState"
        :combat="combatState"
        :space="spaceState"
        :scene-units="sceneUnitsState"
        :dead-units="deadUnitsState"
        :active-ally-id="activeCombatAllyId"
        :send-tactical-move-request="sendTacticalMoveRequest"
        :send-combat-action-request="sendTextMessage"
        :end-combat-turn-request="endCombatTurn"
        @selected-unit-change="handleSelectedUnitChange"
        @request-action-sheet="handleRequestActionSheet"
        @action-notice="handleActionNotice"
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
import { Plus, Trash2 } from 'lucide-vue-next'
import ActionAvailabilityNotice from '../components/Chat/ActionAvailabilityNotice.vue'
import ChatMessage from '../components/Chat/ChatMessage.vue'
import ChatInput from '../components/Chat/ChatInput.vue'
import ActionPanel from '../components/Chat/ActionPanel.vue'
import CharacterSidebar from '../components/Chat/SideCharacterPanel/CharacterSidebar.vue'
import { useActionAvailabilityNotice } from '../composables/useActionAvailabilityNotice'
import { useChatSession } from '../composables/useChatSession'
import { WELCOME_MESSAGE, useChatMessages } from '../composables/useChatMessages'
import { useChatSender } from '../composables/useChatSender'
import type { AvailabilitySelectionUnit } from '../Services_/actionAvailabilityService'
import { chatService } from '../Services_/chatService'
import { defaultLeftRailState, overrideLeftRailMode, publishLeftRailState } from '../Services_/leftRailService'
import { createSession, deleteSession as deleteSessionApi } from '../Services_/sessionService'
import { APP_SETTINGS_UPDATED_EVENT, loadAppSettings, type AppSettings } from '../Services_/SettingsPageService'

import '../styles_/Chatpages.css'

// 右侧面板状态
const containerRef = ref<HTMLElement | null>(null)
const messageListRef = ref<HTMLElement | null>(null)
const characterSidebarRef = ref<InstanceType<typeof CharacterSidebar> | null>(null)
const rightWidth = ref(25)
const showToggleBtn = ref(false)
const isDragging = ref(false)
const appSettings = ref(loadAppSettings())

// 顶部可用性提示只依赖页面壳级状态，不直接读取地图组件内部实现。
const selectedUnit = ref<AvailabilitySelectionUnit | null>(null)
const combatActionSheetRequestId = ref(0)
const manualActionNoticeText = ref('')
let manualActionNoticeTimer: ReturnType<typeof setTimeout> | null = null

// 聊天逻辑
const { sessionId, updateSessionId, clearSessionId } = useChatSession()
const {
  messages,
  pendingAction,
  errorText,
  isSending,
  combatState,
  spaceState,
  sceneUnitsState,
  deadUnitsState,
  playerState,
  debugMode,
  addUserMessage,
  addAssistantMessage,
  addCombatMessage,
  addToolMessage,
  addDiceRollMessage,
  addConfirmedMessage,
  setPendingAction,
  setPlayerState,
  setCombatState,
  setSpaceState,
  setSceneUnitsState,
  setDeadUnitsState,
  setError,
  setSending,
  clearError,
  setMessages,
  resetChatState,
  toggleDebugMode,
  startLoading,
  stopLoading,
} = useChatMessages(appSettings.value.defaultDebugMode)

// 通过 provide 向子组件注入 debugMode
provide('debugMode', debugMode)
provide('skipOutputAnimation', computed(() => appSettings.value.skipOutputAnimation))

// 右侧侧栏始终保持角色信息视图，左侧是否切到战斗时间线由页面壳统一决定。
watch(combatState, () => {
  if (characterSidebarRef.value) {
    characterSidebarRef.value.setViewMode('character')
  }
}, { immediate: true })

const { sendTextMessage, confirmDiceRoll, respondToPlayerDeath, respondToReaction } = useChatSender(
  sessionId,
  updateSessionId,
  addUserMessage,
  addAssistantMessage,
  addCombatMessage,
  addToolMessage,
  addDiceRollMessage,
  addConfirmedMessage,
  setPendingAction,
  setPlayerState,
  setCombatState,
  setSpaceState,
  setSceneUnitsState,
  setDeadUnitsState,
  setError,
  setSending,
  clearError,
  pendingAction,
  startLoading,
  stopLoading
)

// 中文注释：战术移动仍然复用文本消息通道，但这类结构化请求不应显示在聊天列表里。
const sendTacticalMoveRequest = (message: string) => sendTextMessage(message, { silent: true })

// 把顶部提示条的判断完全收口到 composable，聊天页只做挂载和数据透传。
const {
  reasons: actionAvailabilityReasons,
  noticeText: derivedActionAvailabilityText,
} = useActionAvailabilityNotice(
  playerState,
  combatState,
  spaceState,
  selectedUnit,
)

const actionAvailabilityText = computed(() => manualActionNoticeText.value || derivedActionAvailabilityText.value)
const isCombatActive = computed(() => {
  const combat = combatState.value
  if (!combat || typeof combat !== 'object') return false
  const participants = combat.participants
  return !!(participants && typeof participants === 'object' && Object.keys(participants).length > 0)
})

const playerUnitId = computed(() => {
  const player = playerState.value
  if (!player || typeof player !== 'object') return ''
  if (typeof player.id === 'string' && player.id.trim()) return player.id.trim()
  const name = typeof player.name === 'string' ? player.name.trim() : ''
  return name ? `player_${name}` : ''
})

const currentControlledActor = computed<{ id: string; name: string; side: string } | null>(() => {
  if (!combatState.value || pendingAction.value) return null
  const currentActorId: string = combatState.value.current_actor_id || ''
  if (!currentActorId) return null
  if (currentActorId === playerUnitId.value) {
    return { id: currentActorId, name: playerState.value?.name || '玩家', side: 'player' }
  }
  const actor = combatState.value.participants?.[currentActorId]
  if (actor?.side === 'ally') {
    return { id: currentActorId, name: actor.name || currentActorId, side: 'ally' }
  }
  return null
})

const showEndTurnBar = computed(() => !!currentControlledActor.value && !pendingAction.value)
const currentControlledActorName = computed(() => currentControlledActor.value?.name || '当前单位')

const activeCombatAllyId = computed(() => {
  const actor = currentControlledActor.value
  return actor?.side === 'ally' ? actor.id : ''
})

const endCombatTurn = async (actorId: string) => {
  if (!sessionId.value) {
    setError('当前没有可用会话，无法结束回合。')
    return
  }
  clearError()
  setSending(true)
  try {
    await chatService.endCombatTurnStream({
      session_id: sessionId.value,
      actor_id: actorId,
    }, {
      onAssistantMessage: (content) => {
        if (content) addAssistantMessage(content, true)
      },
      onCombatAction: (content, hpChanges) => {
        addCombatMessage(content, hpChanges)
      },
      onToolMessage: (content) => {
        if (content) addToolMessage(content)
      },
      onDiceRoll: async (roll) => {
        addDiceRollMessage(roll)
      },
      onStateUpdate: (player, combat, sceneUnits, deadUnits, space) => {
        if (player !== undefined) setPlayerState(player)
        if (combat !== undefined) setCombatState(combat)
        if (space !== undefined) setSpaceState(space)
        if (sceneUnits !== undefined) setSceneUnitsState(sceneUnits)
        if (deadUnits !== undefined) setDeadUnitsState(deadUnits)
      },
      onPendingAction: (action) => {
        setPendingAction(action)
      },
      onDone: (sid) => {
        if (sid) updateSessionId(sid)
        setSending(false)
      },
      onError: (message) => {
        setError(message)
        setSending(false)
      },
    })
  } catch (error) {
    setError(error instanceof Error ? error.message : '结束回合失败')
    setSending(false)
  } finally {
    // 流式 done 会先释放发送状态；异常或早退时这里兜底。
    setSending(false)
  }
}

const endCurrentControlledTurn = async () => {
  const actor = currentControlledActor.value
  if (!actor) return
  await endCombatTurn(actor.id)
}

const autoScrollDisabled = ref(!appSettings.value.autoScrollChat)

const isNearBottom = (): boolean => {
  const el = messageListRef.value
  if (!el) return true
  const threshold = 50
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

const handleScroll = () => {
  if (!appSettings.value.autoScrollChat) {
    autoScrollDisabled.value = true
    return
  }
  autoScrollDisabled.value = !isNearBottom()
}

const scrollToBottom = () => {
  nextTick(() => {
    if (!appSettings.value.autoScrollChat) return
    if (!autoScrollDisabled.value && messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

// 监听消息变化自动滚动
watch(messages, scrollToBottom, { deep: true })

watch(
  [isCombatActive, playerState, combatState, spaceState, sceneUnitsState, selectedUnit, combatActionSheetRequestId],
  ([combatActive, player, combat, space, sceneUnits, target, requestId]) => {
    if (combatActive) {
      publishLeftRailState({
        mode: 'combat',
        combatVisible: true,
        combat: {
          player,
          combat,
          space,
          sceneUnits,
          selectedUnit: target,
          actionSheetRequestId: requestId,
          requestCombatActionPanel: (preferredTarget) => {
            if (preferredTarget) selectedUnit.value = preferredTarget
            characterSidebarRef.value?.openCombatActionPanel(preferredTarget ?? undefined)
          },
          sendCombatActionRequest: sendTextMessage,
          endCombatTurnRequest: endCombatTurn,
          onActionNotice: handleActionNotice,
        },
      })
      return
    }

    publishLeftRailState(defaultLeftRailState())
  },
  { immediate: true, deep: true },
)

const hydrateCurrentSession = async () => {
  if (!sessionId.value) return

  try {
    const history = await chatService.fetchHistory(sessionId.value)
    const shouldHydrateMessages = messages.value.length === 1
      && messages.value[0]?.role === 'assistant'
      && messages.value[0]?.content === WELCOME_MESSAGE

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
    if ((history as any).space) setSpaceState((history as any).space)
    if ((history as any).scene_units) setSceneUnitsState((history as any).scene_units)
    if ((history as any).dead_units) setDeadUnitsState((history as any).dead_units)
  } catch {
    clearSessionId()
  }
}

const startNewSession = async () => {
  try {
    const session = await createSession()
    updateSessionId(session.id)
    resetChatState()
    clearError()
  } catch (error) {
    setError(error instanceof Error ? error.message : '创建会话失败')
  }
}

const deleteCurrentSession = async () => {
  if (!sessionId.value || !confirm('确定要删除当前会话吗？相关记忆和 trace 会一起清理。')) return

  try {
    await deleteSessionApi(sessionId.value)
    clearSessionId()
    resetChatState()
    clearError()
  } catch (error) {
    setError(error instanceof Error ? error.message : '删除会话失败')
  }
}

onMounted(async () => {
  document.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('storage', handleSettingsStorage)
  window.addEventListener(APP_SETTINGS_UPDATED_EVENT, handleSettingsUpdated as EventListener)

  const pendingSessionId = sessionStorage.getItem('pending_session_id')
  if (pendingSessionId) {
    updateSessionId(pendingSessionId)
    sessionStorage.removeItem('pending_session_id')
  }

  await hydrateCurrentSession()
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
  window.removeEventListener('storage', handleSettingsStorage)
  window.removeEventListener(APP_SETTINGS_UPDATED_EVENT, handleSettingsUpdated as EventListener)
  if (manualActionNoticeTimer) clearTimeout(manualActionNoticeTimer)
  publishLeftRailState(defaultLeftRailState())
})

const handleSettingsStorage = (event: StorageEvent) => {
  if (event.key !== 'trpg-app-settings') return
  applySettings(loadAppSettings())
}

const handleSettingsUpdated = (event: CustomEvent<AppSettings>) => {
  applySettings(event.detail)
}

const applySettings = (settings: AppSettings) => {
  appSettings.value = settings
  debugMode.value = settings.defaultDebugMode
  autoScrollDisabled.value = !settings.autoScrollChat
  if (settings.autoScrollChat) {
    scrollToBottom()
  }
}

const togglePanel = () => {
  rightWidth.value = rightWidth.value === 0 ? 25 : 0
}

// 地图侧栏只上抛“当前选中的单位”语义，页面用它驱动可用性提示，不反向耦合地图细节。
const handleSelectedUnitChange = (unit: AvailabilitySelectionUnit | null) => {
  selectedUnit.value = unit
}

// 中文注释：双击敌人时只增加一次“打开动作面板”的触发信号，避免把行为文本塞进聊天消息。
const handleRequestActionSheet = (unit: AvailabilitySelectionUnit) => {
  selectedUnit.value = unit
  combatActionSheetRequestId.value += 1
  characterSidebarRef.value?.openCombatActionPanel(unit)
  overrideLeftRailMode('combat')
}

const handleActionNotice = (text: string) => {
  manualActionNoticeText.value = text
  if (manualActionNoticeTimer) clearTimeout(manualActionNoticeTimer)
  manualActionNoticeTimer = setTimeout(() => {
    manualActionNoticeText.value = ''
    manualActionNoticeTimer = null
  }, 2600)
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
