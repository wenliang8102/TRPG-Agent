<template>
  <div class="app-layout">
    <!-- 收缩按钮（左上角，靠近才显示） -->
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

    <!-- 全局骰子按钮（右下角悬浮） -->
    <button class="dice-fab" @click="diceVisible = true">
      🎲
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, shallowRef } from 'vue'
import Sidebar from './Sidebar.vue'
import WelcomePage from '../Welcome/WelcomePage.vue'
import ChatPage from '../Chat/ChatPage.vue'
import DiceDialog from '../DiceDialog/DiceDialog.vue'

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
  page7: WelcomePage,
  profile: WelcomePage
}

const currentTab = ref('welcome')
const currentComponent = computed(() => componentMap[currentTab.value] || WelcomePage)

const isCollapsed = ref(false)
const isHovering = ref(false)
const diceVisible = ref(false)

const toggleCollapse = () => {
  isCollapsed.value = !isCollapsed.value
}

const handleSelect = (tabId: string) => {
  currentTab.value = tabId
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

/* 右侧内容区 - 可滚动 */
.main-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  background: #0d0d0d;
  position: relative;
}

/* 滚动条样式（iOS 风格） */
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

.main-content::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.3);
}

/* 内容滚动容器 */
.content-scroll {
  min-height: 100%;
  width: 100%;
}

/* 收缩按钮：紧靠浏览器左边，在用户头像上方 2.5px 处 */
.collapse-btn {
  position: fixed;
  bottom: calc(12px + 20px + 56px + 12px); /* 用户头像位置 + 2.5px */
  left: 0;
  z-index: 200;
  width: 32px;
  height: 32px;
  border-radius: 0 12px 12px 0;
  background: rgba(30, 30, 35, 0.9);
  backdrop-filter: blur(20px);
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


/* 右下角悬浮骰子按钮（iOS 风格） */
.dice-fab {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: rgba(66, 184, 131, 0.95);
  backdrop-filter: blur(20px);
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  font-size: 28px;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 99;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dice-fab:hover {
  transform: scale(1.08);
  background: #359f6b;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}
</style>