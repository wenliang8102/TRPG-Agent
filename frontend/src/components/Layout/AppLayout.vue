<!-- frontend/src/components/Layout/AppLayout.vue -->
<template>
  <div class="app-layout">
    <!-- 收缩按钮 -->
    <button
      v-if="showCollapseButton"
      class="collapse-btn"
      :class="{ visible: isHovering }"
      @click="toggleCollapse"
      @mouseenter="isHovering = true"
      @mouseleave="isHovering = false"
    >
      {{ isCollapsed ? '→' : '←' }}
    </button>

    <!-- 左侧导航栏 -->
    <Sidebar
      v-if="leftRailMode === 'navigation'"
      :is-collapsed="isCollapsed"
      :current-tab="currentTab"
      @select="handleSelect"
    />
    <CombatTimelinePanel
      v-else
      :player="leftRailState.combat?.player ?? null"
      :combat="leftRailState.combat?.combat ?? null"
      :space="leftRailState.combat?.space ?? null"
      :scene-units="leftRailState.combat?.sceneUnits ?? null"
      :selected-unit="leftRailState.combat?.selectedUnit ?? null"
      :action-sheet-request-id="leftRailState.combat?.actionSheetRequestId ?? 0"
      :request-combat-action-panel="leftRailState.combat?.requestCombatActionPanel ?? null"
      :is-collapsed="isCollapsed"
      @action-notice="forwardActionNotice"
    />

    <!-- 右侧内容区 -->
    <main class="main-content">
      <component 
    :is="currentComponent" 
    @navigate="handleNavigate"
    />
    </main>

    <!-- 骰子弹窗 -->
    <DiceDialog
      :visible="diceVisible"
      @close="diceVisible = false"
    />

    <!-- 骰子动画页面 -->
    <DiceAnimationPage v-if="diceAnimationVisible" @close="diceAnimationVisible = false" />

    <!-- 全局骰子按钮（右下角悬浮，样式改为导航栏风格） -->
       <!--
    <button class="dice-fab" @click="openDiceAnimation">
      🎲
    </button>
         -->
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import Sidebar from './Sidebar.vue'
import CombatTimelinePanel from '../Chat/Combat/CombatTimelinePanel.vue'
import WelcomePage from '../../Pages_/WelcomePage.vue'
import ChatPage from '../../Pages_/Chatpages.vue'
import SettingsPage from '../../Pages_/SettingsPage.vue'
import DiceDialog from '../DiceDialog/DiceDialog.vue'
import DiceAnimationPage from '../../Pages_/DiceAnimationPage.vue'
import HistoryPage from '../../Pages_/HistoryPage.vue'
import RulePage from '../../Pages_/RulePage.vue'
import ProfilePage from '../../Pages_/ProfilePage.vue'
import {
  defaultLeftRailState,
  LEFT_RAIL_OVERRIDE_EVENT,
  LEFT_RAIL_UPDATED_EVENT,
  notifyLeftRailMode,
} from '../../Services_/leftRailService'
import type { LeftRailState } from './leftRailTypes'

// 在 script 中添加
const handleNavigate = (tabId: string, params?: Record<string, any>) => {
  currentTab.value = tabId
  // 可以将 params 存储到全局状态（如 provide/inject 或 pinia），供 ChatPage 读取 session_id
  // 简单起见，这里通过 sessionStorage 传递
  if (params?.session_id) {
    sessionStorage.setItem('pending_session_id', params.session_id)
  }
}

// 仅保留实际存在且可用的页面，移除占位入口。
const componentMap: Record<string, any> = {
  welcome: WelcomePage,
  chat: ChatPage,
  history: HistoryPage,
  rules: RulePage,
  settings: SettingsPage,
  profile: ProfilePage,
}

const currentTab = ref('welcome')
const currentComponent = computed(() => componentMap[currentTab.value] || WelcomePage)
const leftRailState = ref<LeftRailState>(defaultLeftRailState())
const forceNavigationRail = ref(false)

const isCollapsed = ref(false)
const isHovering = ref(false)

// 骰子相关
const diceVisible = ref(false)
const diceAnimationVisible = ref(false)

const toggleCollapse = () => {
  isCollapsed.value = !isCollapsed.value
}

const handleSelect = (tabId: string) => {
  currentTab.value = tabId
}

const handleLeftRailUpdated = (event: Event) => {
  const detail = (event as CustomEvent<LeftRailState>).detail
  leftRailState.value = detail ?? defaultLeftRailState()
  if (!leftRailState.value.combatVisible) {
    forceNavigationRail.value = false
  }
}

const forwardActionNotice = (text: string) => {
  leftRailState.value.combat?.onActionNotice?.(text)
}

const handleLeftRailOverride = (event: Event) => {
  const mode = (event as CustomEvent<'navigation' | 'combat' | null>).detail
  if (mode === 'navigation') {
    forceNavigationRail.value = true
    return
  }
  if (mode === 'combat') {
    forceNavigationRail.value = false
    return
  }
  forceNavigationRail.value = false
}

const leftRailMode = computed<'navigation' | 'combat'>(() => {
  if (leftRailState.value.combatVisible && !forceNavigationRail.value) {
    return 'combat'
  }
  return 'navigation'
})

const showCollapseButton = computed(() => currentTab.value === 'chat')

onMounted(() => {
  window.addEventListener(LEFT_RAIL_UPDATED_EVENT, handleLeftRailUpdated as EventListener)
  window.addEventListener(LEFT_RAIL_OVERRIDE_EVENT, handleLeftRailOverride as EventListener)
})

onUnmounted(() => {
  window.removeEventListener(LEFT_RAIL_UPDATED_EVENT, handleLeftRailUpdated as EventListener)
  window.removeEventListener(LEFT_RAIL_OVERRIDE_EVENT, handleLeftRailOverride as EventListener)
})

watch(leftRailMode, (mode) => {
  notifyLeftRailMode(mode)
}, { immediate: true })

</script>

<style scoped>
.app-layout {
  display: flex;
  width: 100%;
  height: 100vh;
  position: relative;
  overflow: hidden;
  background: #0d0d0d;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  background: rgba(13, 13, 13, 0.85);
  backdrop-filter: blur(4px);
  position: relative;
}

.main-content::-webkit-scrollbar {
  width: 6px;
}

.main-content::-webkit-scrollbar-track {
  background: transparent;
}

.main-content::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 10px;
}

.collapse-btn {
  position: fixed;
  bottom: calc(12px + 20px + 56px + 12px);
  left: 0;
  z-index: 200;
  width: 32px;
  height: 32px;
  border-radius: 0 12px 12px 0;
  background: rgba(30, 30, 35, 0.85);
  backdrop-filter: blur(12px);
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  border-left: none;
  color: white;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.collapse-btn.visible {
  opacity: 1;
}

.collapse-btn:hover {
  background: rgba(66, 184, 131, 0.9);
  width: 36px;
}

/* 骰子按钮玻璃质感增强 */
.dice-fab {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: rgba(30, 30, 35, 0.85);
  backdrop-filter: blur(12px);
  border: 0.5px solid rgba(255, 255, 255, 0.25);
  font-size: 28px;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 99;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #e6e6c3;
}

.dice-fab:hover {
  background: #8b5a2b;
  transform: scale(1.08);
  border-color: #b87c4f;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}
</style>
