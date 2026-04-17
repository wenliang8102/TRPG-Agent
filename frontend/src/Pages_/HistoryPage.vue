<!-- frontend/src/Pages_/HistoryPage.vue -->
<template>
 <div class="history-page">
    <!-- 上方固定区域 -->
    <div class="history-header">
      <div class="header-content">
        <!-- 主标题（扭曲开裂效果） -->
        <h1 class="page-title cracked-title">编年史</h1>
        <!-- 副标题已移除 -->

        <!-- 紧凑型搜索栏 -->
        <div class="search-section">
          <div class="search-box">
            <Search :size="16" class="search-icon" />
            <input
              v-model="searchKeyword"
              type="text"
              placeholder="搜索会话名称、内容关键词..."
              class="search-input"
              @input="handleSearch"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 向两边渐隐的分隔线 -->
    <div class="divider-line"></div>

    <!-- 下方可滚动内容区域 -->
    <div class="history-content">
      <!-- 加载状态 -->
      <div v-if="isLoading" class="loading-state">
        <div class="spinner"></div>
        <p>翻阅历史卷轴中...</p>
      </div>

      <!-- 会话网格 -->
      <div v-else-if="filteredSessions.length > 0" class="sessions-grid">
        <div
          v-for="session in filteredSessions"
          :key="session.id"
          class="session-card"
          @click="enterSession(session.id)"
        >
          <div class="card-header">
            <span class="session-title">{{ session.title }}</span>
            <span class="session-time">{{ formatTime(session.lastMessageAt) }}</span>
          </div>
          <p class="session-preview">{{ session.preview || '暂无消息记录' }}</p>
          <div class="card-footer">
            <span class="message-count">
              <MessageCircle :size="14" class="footer-icon" />
              {{ session.messageCount }} 条
            </span>
            <button
              class="delete-btn"
              @click.stop="deleteSession(session.id)"
              title="删除此冒险记录"
            >
              <Trash2 :size="16" />
            </button>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else class="empty-state">
        <div class="empty-icon">📭</div>
        <p>{{ searchKeyword ? '未找到匹配的冒险记录' : '暂无历史对话' }}</p>
        <p class="empty-hint" v-if="!searchKeyword">去“聊天助手”开启新的征程吧！</p>
        <button class="primary-btn" @click="goToChat">
          <MessageCircle :size="18" />
          前往聊天
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Search, MessageCircle, Trash2 } from 'lucide-vue-next'
import { listSessions, deleteSession as deleteSessionApi, type ChatSession } from '../Services_/sessionService'

const emit = defineEmits<{
  navigate: [tabId: string, params?: Record<string, any>]
}>()

const isLoading = ref(true)
const sessions = ref<ChatSession[]>([])

// 搜索关键词
const searchKeyword = ref('')

// 过滤后的会话列表（仅按关键词）
const filteredSessions = computed(() => {
  let result = sessions.value

  if (searchKeyword.value.trim()) {
    const keyword = searchKeyword.value.trim().toLowerCase()
    result = result.filter(s =>
      s.title.toLowerCase().includes(keyword) ||
      (s.preview && s.preview.toLowerCase().includes(keyword))
    )
  }

  // 按最后消息时间倒序排列
  return result.sort((a, b) => b.lastMessageAt - a.lastMessageAt)
})

// 搜索防抖
let searchTimer: ReturnType<typeof setTimeout>
const handleSearch = () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    // 搜索已在 computed 中处理
  }, 300)
}

onMounted(async () => {
  try {
    sessions.value = await listSessions()
  } catch (error) {
    console.error('获取会话列表失败:', error)
    sessions.value = []
  } finally {
    isLoading.value = false
  }
})

const formatTime = (timestamp: number): string => {
  const date = new Date(timestamp)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))

  if (days === 0) return '今天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  return date.toLocaleDateString('zh-CN')
}

const enterSession = (sessionId: string) => {
  emit('navigate', 'chat', { session_id: sessionId })
}

const deleteSession = async (sessionId: string) => {
  if (!confirm('确定要删除这段冒险记录吗？')) return
  try {
    await deleteSessionApi(sessionId)
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
  } catch (error) {
    console.error('删除失败:', error)
  }
}

const goToChat = () => {
  emit('navigate', 'chat')
}
</script>

<style scoped>

/* 引入中世纪装饰字体 */
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&display=swap');
/* 可额外引入一种破损风格字体，若无则用 Cinzel 配合效果 */
@import url('https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&display=swap');

* {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}

/* 开裂扭曲标题样式 */
.cracked-title {
  font-family: 'Cinzel', 'UnifrakturMaguntia', serif;
  font-size: 2.6rem;
  font-weight: 700;
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: 4px;
  text-shadow: 
    2px 2px 0 rgba(0, 0, 0, 0.3),
    -1px -1px 0 rgba(255, 255, 255, 0.1),
    4px 4px 8px rgba(0, 0, 0, 0.5);
  transform: skew(-2deg) rotate(-0.5deg);
  position: relative;
  display: inline-block;
  margin: 0 0 8px 0;
  line-height: 1.2;
  /* 开裂纹理叠加 */
  &::after {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: repeating-linear-gradient(
      45deg,
      transparent,
      transparent 3px,
      rgba(0, 0, 0, 0.08) 3px,
      rgba(0, 0, 0, 0.15) 6px
    );
    pointer-events: none;
    mix-blend-mode: multiply;
    border-radius: 4px;
  }
  /* 模拟裂纹线条 */
  &::before {
    content: '';
    position: absolute;
    top: 20%;
    left: 5%;
    width: 90%;
    height: 2px;
    background: rgba(0, 0, 0, 0.3);
    box-shadow: 
      0 8px 0 rgba(0, 0, 0, 0.2),
      0 16px 0 rgba(0, 0, 0, 0.1),
      20px 12px 0 rgba(0, 0, 0, 0.15),
      -10px 25px 0 rgba(0, 0, 0, 0.1);
    transform: rotate(-1deg);
    opacity: 0.6;
    pointer-events: none;
  }
}

/* 响应式调整 */
@media (max-width: 700px) {
  .cracked-title {
    font-size: 2rem;
  }
}

/* 全局基础字体：优先使用系统无衬线字体，确保兼容性 */
* {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}

.history-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  background: transparent;
  color: #e5e5ea;
}

/* ========== 上方固定区域（紧凑） ========== */
.history-header {
  flex: 0 0 auto;
  padding: 20px 24px 8px 24px;
}

.header-content {
  max-width: 1400px;
  margin: 0 auto;
}

.page-title {
  margin: 0;
  font-size: 2.2rem;
  font-weight: 600;
  font-family: 'Cinzel', serif;  /* 装饰字体仅用于标题 */
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: 2px;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.3);
  line-height: 1.2;
}

.page-subtitle {
  margin: 2px 0 12px 0;
  color: #a1a1aa;
  font-size: 0.9rem;
  font-style: italic;
}

/* 搜索区域（缩小版） */
.search-section {
  display: flex;
  align-items: center;
  margin-bottom: 4px;
}

.search-box {
  width: 100%;
  max-width: 420px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(30, 30, 35, 0.5);
  backdrop-filter: blur(10px);
  border: 0.5px solid rgba(255, 255, 255, 0.12);
  border-radius: 30px;
  padding: 4px 14px;
  transition: all 0.2s;
}

.search-box:focus-within {
  border-color: rgba(66, 184, 131, 0.5);
  box-shadow: 0 0 0 2px rgba(66, 184, 131, 0.1);
  background: rgba(40, 40, 48, 0.6);
}

.search-icon {
  color: #8e8e93;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  color: #e5e5ea;
  font-size: 0.9rem;
  outline: none;
  padding: 6px 0;
  font-family: inherit;  /* 继承全局字体 */
}

.search-input::placeholder {
  color: #6c6c70;
  font-style: italic;
  font-size: 0.85rem;
}

/* ========== 渐隐分隔线 ========== */
.divider-line {
  height: 1px;
  width: 100%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.05) 15%,
    rgba(255, 215, 180, 0.15) 50%,
    rgba(255, 255, 255, 0.05) 85%,
    transparent 100%
  );
  margin: 4px 0 0 0;
}

/* ========== 下方滚动区域 ========== */
.history-content {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 16px 24px 24px 24px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
}

.history-content::-webkit-scrollbar {
  width: 5px;
}

.history-content::-webkit-scrollbar-track {
  background: transparent;
}

.history-content::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 10px;
}

.history-content::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.25);
}

/* 加载状态 */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
  color: #a1a1aa;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(255, 255, 255, 0.08);
  border-top-color: #42b883;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 会话网格 */
.sessions-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

@media (max-width: 1400px) {
  .sessions-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}

@media (max-width: 1000px) {
  .sessions-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 700px) {
  .sessions-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 480px) {
  .sessions-grid {
    grid-template-columns: 1fr;
  }
}

/* 会话卡片 */
.session-card {
  background: rgba(25, 25, 32, 0.7);
  backdrop-filter: blur(10px);
  border: 0.5px solid rgba(255, 255, 255, 0.08);
  border-radius: 18px;
  padding: 18px;
  cursor: pointer;
  transition: all 0.25s ease;
  display: flex;
  flex-direction: column;
  height: 180px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.session-card:hover {
  background: rgba(35, 35, 45, 0.8);
  border-color: rgba(66, 184, 131, 0.3);
  transform: translateY(-4px);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.4);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}

.session-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: #f0e6d0;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 65%;
}

.session-time {
  font-size: 0.7rem;
  color: #8e8e93;
  background: rgba(0, 0, 0, 0.2);
  padding: 3px 8px;
  border-radius: 20px;
  white-space: nowrap;
}

.session-preview {
  flex: 1;
  color: #b0b0b8;
  font-size: 0.85rem;
  line-height: 1.5;
  margin: 0 0 12px 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  word-break: break-word;
}

.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  border-top: 0.5px solid rgba(255, 255, 255, 0.05);
  padding-top: 10px;
}

.message-count {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  color: #8e8e93;
}

.footer-icon {
  color: #6c6c70;
}

.delete-btn {
  background: transparent;
  border: none;
  color: #8e8e93;
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  opacity: 0.5;
}

.delete-btn:hover {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
  opacity: 1;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
  color: #a1a1aa;
  text-align: center;
}

.empty-icon {
  font-size: 4rem;
  margin-bottom: 16px;
  opacity: 0.4;
}

.empty-hint {
  margin: 8px 0 24px;
  font-size: 0.9rem;
  color: #7a7a82;
}

.primary-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(66, 184, 131, 0.12);
  border: 0.5px solid rgba(66, 184, 131, 0.3);
  color: #42b883;
  padding: 10px 26px;
  border-radius: 40px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.primary-btn:hover {
  background: rgba(66, 184, 131, 0.25);
  transform: translateY(-2px);
  box-shadow: 0 6px 12px rgba(0, 0, 0, 0.3);
}
</style>