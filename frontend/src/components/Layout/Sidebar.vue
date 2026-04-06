<template>
  <aside :class="['sidebar', { collapsed: isCollapsed }]">
    <div class="sidebar-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        :class="['nav-btn', { active: currentTab === item.id }]"
        @click="$emit('select', item.id)"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span v-if="!isCollapsed" class="nav-label">{{ item.label }}</span>
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
const props = defineProps<{
  isCollapsed: boolean
  currentTab: string
}>()

const emit = defineEmits<{
  select: [tabId: string]
}>()

const navItems = [
  { id: 'welcome', label: '欢迎', icon: '🏠' },
  { id: 'chat', label: '聊天助手', icon: '💬' },
  { id: 'page1', label: '1', icon: '1️' },
  { id: 'page2', label: '2', icon: '2️' },
  { id: 'page3', label: '3', icon: '3️' },
  { id: 'page4', label: '4', icon: '4️' },
  { id: 'page5', label: '5', icon: '5️' },
  { id: 'page6', label: '6', icon: '6️' },
  { id: 'page7', label: '7', icon: '7️' },
  { id: 'profile', label: '用户', icon: '👤' }
]
</script>

<style scoped>
.sidebar {
  width: 240px;
  height: 100vh;
  background: rgba(30, 30, 35, 0.95);
  backdrop-filter: blur(20px);
  border-right: 0.5px solid rgba(255, 255, 255, 0.1);
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  left: 0;
  display:flex;
}

.sidebar.collapsed {
  width: 72px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  width: 230px;
  margin-top: 3.3px;
  margin-bottom: 3.3px;
  margin-left: 3.3px;
  margin-right: 3.3px;
}

.nav-btn {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  background: transparent;
  border: none;
  border-radius: 14px;
  color: #8e8e93;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  width: 100%;
  font-size: 16px;
}

.nav-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.nav-btn.active {
  background: rgba(66, 184, 131, 0.25);
  color: #42b883;
}

.sidebar.collapsed .nav-btn {
  justify-content: center;
  padding: 12px;
}

.nav-icon {
  font-size: 22px;
  min-width: 28px;
  text-align: center;
}

.nav-label {
  font-size: 15px;
  font-weight: 500;
  white-space: nowrap;
}



/* ========== 10 个按钮的 CSS 选择器,改单独样式 ========== */

/* 第 1 个按钮（欢迎） */
.nav-btn:first-child {
  /* 保持原样，后面改 */
}

/* 第 2 个按钮（聊天助手） */
.nav-btn:nth-child(2) {
  /* 保持原样，后面改 */
}

/* 第 3 个按钮（1） */
.nav-btn:nth-child(3) {
  /* 保持原样，后面改 */
}

/* 第 4 个按钮（2） */
.nav-btn:nth-child(4) {
  /* 保持原样，后面改 */
}

/* 第 5 个按钮（3） */
.nav-btn:nth-child(5) {
  /* 保持原样，后面改 */
}

/* 第 6 个按钮（4） */
.nav-btn:nth-child(6) {
  /* 保持原样，后面改 */
}

/* 第 7 个按钮（5） */
.nav-btn:nth-child(7) {
  /* 保持原样，后面改 */
}

/* 第 8 个按钮（6） */
.nav-btn:nth-child(8) {
  /* 保持原样，后面改 */
}

/* 第 9 个按钮（7） */
.nav-btn:nth-child(9) {
  /* 保持原样，后面改 */
}

/* 第 10 个按钮（用户头像）- 固定到底部，iOS 风格，金色粉末发光 */
.nav-btn:last-child {
  margin-top: auto;
  position: relative;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.25, 0.1, 0.2, 1);
}

/* 第 10 个按钮（用户头像）- 固定到底部，边框金色粉末发光 */
.nav-btn:last-child {
  margin-top: auto;
  margin-bottom: 12px;
  margin-left: 4px;
  margin-right: 4px;
  width: calc(100% - 8px);
  position: relative;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.25, 0.1, 0.2, 1);
  border: 1px solid rgba(255, 215, 0, 0.3);
  box-shadow: 0 0 4px rgba(255, 215, 0, 0.2);
}

/* 鼠标悬停效果 - 边框金色粉末发光 */
.nav-btn:last-child:hover {
  border: 1px solid rgba(255, 215, 0, 0.8);
  box-shadow: 
    0 0 8px rgba(255, 215, 0, 0.5),
    0 0 16px rgba(255, 180, 0, 0.3),
    inset 0 0 4px rgba(255, 215, 0, 0.2);
  background: rgba(255, 215, 0, 0.05);
  transform: translateY(-2px);
}

/* 收缩状态下的适配 */
.sidebar.collapsed .nav-btn:last-child {
  margin-left: 1.3px;
  margin-right: 1.3px;
  width: calc(100% - 8px);
  justify-content: center;
}

.sidebar.collapsed .nav-btn:last-child:hover {
  transform: scale(1.05);
}
</style>
