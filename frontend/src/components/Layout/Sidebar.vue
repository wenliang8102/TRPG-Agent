<!-- frontend/src/components/Layout/Sidebar.vue -->
<template>
  <aside :class="['sidebar', { collapsed: isCollapsed }]">
    <!-- 顶部标题：仅展开时显示，不可选中，中世纪风格字体 -->
    <div v-if="!isCollapsed" class="sidebar-header">
      <h1 class="brand-title">TRPG-AGENT</h1>
    </div>

    <div class="sidebar-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        :class="['nav-btn', { active: currentTab === item.id }]"
        @click="$emit('select', item.id)"
      >
        <component 
          :is="item.icon" 
          class="nav-icon"
          :size="20"
          stroke-width="1.5"
          stroke="currentColor"
          fill="none"
        />
        <span v-if="!isCollapsed" class="nav-label">{{ item.label }}</span>
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { 
  Home, 
  MessageCircle, 
  Hash, 
  Star,
  BookOpen,
  Sword,
  Trophy,
  BarChart3,
  Settings,
  User,
  type LucideIcon
} from 'lucide-vue-next'

// 导入原有基础样式（保留原布局和按钮行为）
import '../../styles_/sidebar.css'

const props = defineProps<{
  isCollapsed: boolean
  currentTab: string
}>()

const emit = defineEmits<{
  select: [tabId: string]
}>()

interface NavItem {
  id: string
  label: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { id: 'welcome', label: '欢迎', icon: Home },
  { id: 'chat', label: '聊天助手', icon: MessageCircle },
  { id: 'page1', label: '1', icon: Hash },
  { id: 'page2', label: '2', icon: Star },
  { id: 'page3', label: '历史', icon: BookOpen },
  { id: 'page4', label: '4', icon: Sword },
  { id: 'page5', label: '5', icon: Trophy },
  { id: 'page6', label: '规则', icon: BookOpen },
  { id: 'page7', label: '设置', icon: Settings },
  { id: 'profile', label: '用户', icon: User },
]
</script>

<style scoped>
/* 引入 Google Fonts 中世纪风格字体 */
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=UnifrakturMaguntia&display=swap');

/* 玻璃质感 + 标题样式（中世纪风格） */
.sidebar {
  position: relative;
  background: rgba(20, 20, 25, 0.7) !important;
  backdrop-filter: blur(16px) !important;
  border-right: 1px solid rgba(255, 255, 255, 0.1) !important;
  box-shadow: 4px 0 20px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 标题容器 */
.sidebar-header {
  padding: 28px 16px 16px 24px;
  margin-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

/* 黑魂中世纪风格字体 */
.brand-title {
  margin: 0;
  font-size: 1.8rem;
  font-weight: 600;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'MedievalSharp', 'Germania One', serif;
  letter-spacing: 3px;
  text-transform: uppercase;
  text-align: center;          /* 添加这一行：水平居中 */
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 2px 2px 6px rgba(0, 0, 0, 0.5);
  transform: skew(-2deg) rotate(-1deg);
  transition: all 0.2s ease;
  user-select: none;
  cursor: default;
}


/* 导航区域自适应滚动 */
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0px;
}

.sidebar-nav::-webkit-scrollbar {
  width: 4px;
}

.sidebar-nav::-webkit-scrollbar-track {
  background: transparent;
}

.sidebar-nav::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 0px;
}

/* 确保折叠时侧边栏仍然保持玻璃质感 */
.sidebar.collapsed {
  background: rgba(20, 20, 25, 0.75) !important;
  backdrop-filter: blur(16px) !important;
}

/* 按钮微调，提升玻璃背景下的可读性 */
.nav-btn {
  background: transparent !important;
  transition: all 0.2s;
  width: 100%;
  border-radius: 0 ;
}

.nav-btn:hover {
  background: rgba(255, 255, 255, 0.08) !important;
  backdrop-filter: blur(4px);
  width: 100%;
  border-radius: 0;
}


.nav-btn.active {
  background: rgba(255, 255, 255, 0.1) !important;   /* 极淡透明白色背景 */
  border-left: 2px solid rgba(255, 255, 255, 0.8);   /* 左边框改为亮白 */
  box-shadow: 0 0 6px rgba(255, 255, 255, 0.3);     /* 可选：极淡外发光，增强白光感 */
}

</style>