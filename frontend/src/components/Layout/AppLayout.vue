<!-- frontend/src/components/Layout/AppLayout.vue -->
<template>
  <div class="app-layout">
    <!-- 收缩按钮 -->
    <button
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
      :is-collapsed="isCollapsed"
      :current-tab="currentTab"
      @select="handleSelect"
    />

    <!-- 右侧内容区 -->
    <main class="main-content">
      <component :is="currentComponent" />
    </main>

    <!-- 骰子弹窗 -->
    <DiceDialog
      :visible="diceVisible"
      @close="diceVisible = false"
    />

    <!-- 骰子动画页面 -->
    <DiceAnimationPage v-if="diceAnimationVisible" @close="diceAnimationVisible = false" />

    <!-- 全局骰子按钮（右下角悬浮，样式改为导航栏风格） -->
    <button class="dice-fab" @click="openDiceAnimation">
      🎲
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import Sidebar from './Sidebar.vue'
import WelcomePage from '../../Pages_/WelcomePage.vue'
import ChatPage from '../../Pages_/Chatpages.vue'
import SettingsPage from '../../Pages_/SettingsPage.vue'
import DiceDialog from '../DiceDialog/DiceDialog.vue'
import DiceAnimationPage from '../../Pages_/DiceAnimationPage.vue'

// 页面组件映射
const componentMap: Record<string, any> = {
  welcome: WelcomePage,
  chat: ChatPage,
  page1: WelcomePage,
  page2: WelcomePage,
  page3: WelcomePage,
  page4: WelcomePage,
  page5: WelcomePage,
  page6: WelcomePage,
  page7: SettingsPage,
  profile: WelcomePage
}

const currentTab = ref('welcome')
const currentComponent = computed(() => componentMap[currentTab.value] || WelcomePage)

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

// 打开骰子动画页面
const openDiceAnimation = () => {
  diceAnimationVisible.value = true
}

// 关闭骰子动画页面（可选）
const closeDiceAnimation = () => {
  diceAnimationVisible.value = false
}
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