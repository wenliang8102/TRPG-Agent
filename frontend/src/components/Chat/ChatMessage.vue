<template>
  <!-- 调试消息 -->
  <div v-if="message.type === 'tool'" v-show="debugMode" class="tool-message-wrapper">
    <div class="tool-badge">TOOL</div>
    <pre class="tool-content">{{ message.content }}</pre>
  </div>

  <!-- 普通消息 -->
  <div v-else :class="['message-wrapper', message.role]">
    <div class="avatar">
      <img v-if="avatarUrl" :src="avatarUrl" :alt="displayName" />
      <div v-else class="avatar-placeholder">{{ avatarIcon }}</div>
    </div>
    <div class="message-content-wrapper">
      <div class="message-header">
        <span class="display-name">{{ displayName }}</span>
        <span class="timestamp">{{ formatTime(message.timestamp) }}</span>
      </div>
      <div class="message-bubble" :class="{ 'combat-bubble': message.type === 'combat_action' }" @click="handleMessageClick">
        <div v-if="message.content" class="message-text" v-html="renderedContent"></div>
        <HpBar
          v-for="(hpc, i) in hpChanges"
          :key="i"
          :name="hpc.name"
          :old-hp="hpc.old_hp"
          :new-hp="hpc.new_hp"
          :max-hp="hpc.max_hp"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, watch, onUnmounted } from 'vue'
import { marked } from 'marked'
import type { ChatMessage } from '../../Services_/chatService'
import HpBar from './HpBar.vue'
import { adaptLLMOutput } from '../../composables/markdownAdapter'
import { useTypewriter } from '../../composables/useTypewriter'

marked.setOptions({
  breaks: true,
  gfm: true,
})

const props = defineProps<{
  message: ChatMessage
}>()

const debugMode = inject<boolean>('debugMode', false)

const hpChanges = computed(() => props.message.metadata?.hp_changes ?? [])
const avatarUrl = computed(() => props.message.avatar ?? undefined)

const displayName = computed(() => {
  if (props.message.displayName) return props.message.displayName
  return props.message.role === 'user' ? '我' : 'TRPG 助手'
})

const avatarIcon = computed(() => props.message.role === 'user' ? '👤' : '🤖')

const formatTime = (timestamp?: string | number) => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

// 预处理内容（列表适配 + 关键词高亮）
const rawProcessedContent = computed(() => {
  if (!props.message.content) return ''
  return adaptLLMOutput(props.message.content)
})

const isUser = props.message.role === 'user'
const isHistory = props.message.isHistory === true   // 判断是否为历史消息

// 只有用户消息或历史消息才跳过动画
const skipAnimation = computed(() => isUser || isHistory)

// 打字机效果（注意：不再依赖 props.isStreaming）
const { displayText, skip, cleanup, reset, flush } = useTypewriter(
  rawProcessedContent,
  35,        // 毫秒/字符，可调整速度
  undefined,
  skipAnimation
)

// 监听消息内容变化，检测新消息开始（可选，用于重置状态）
watch(() => props.message.content, (newContent, oldContent) => {
  // 如果内容长度突然变短（新消息覆盖），重置打字机
  if (oldContent && newContent.length < oldContent.length) {
    reset()
  }
}, { immediate: false })

// 渲染内容：历史消息或用户消息直接显示完整内容，否则显示打字机效果
const renderedContent = computed(() => {
  const content = skipAnimation.value ? rawProcessedContent.value : displayText.value
  if (!content) return ''
  return marked.parse(content, { async: false }) as string
})

// 点击消息跳过动画（仅当动画播放时有效）
const handleMessageClick = () => {
  if (!skipAnimation.value) {
    skip()
  }
}

onUnmounted(() => {
  cleanup()
})
</script>

<style scoped>
/* 保持原有样式不变，只添加淡入动画 */
.message-wrapper {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  max-width: 100%;
  animation: fadeInUp 0.3s ease-out;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message-wrapper {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  max-width: 100%;
}

.message-wrapper.user {
  flex-direction: row-reverse;
}

.message-wrapper.user .message-content-wrapper {
  align-items: flex-end;
}

.message-wrapper.user .message-header {
  flex-direction: row-reverse;
}

.message-wrapper.user .message-bubble {
  background: rgba(66, 184, 131, 0.15);
  border: 0.5px solid rgba(66, 184, 131, 0.3);
}

.message-wrapper.assistant {
  flex-direction: row;
}

.message-wrapper.assistant .message-bubble {
  background: rgba(45, 45, 55, 0.8);
  border: 0.5px solid rgba(255, 255, 255, 0.1);
}

.avatar {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  overflow: hidden;
}

.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: rgba(66, 184, 131, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}

.message-content-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  max-width: calc(100% - 48px);
}

.message-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 6px;
}

.display-name {
  font-size: 14px;
  font-weight: 600;
  color: #e5e5ea;
}

.timestamp {
  font-size: 11px;
  color: #6c6c70;
}

.message-bubble {
  padding: 10px 14px;
  border-radius: 16px;
  border-top-left-radius: 4px;
  max-width: 100%;
  word-wrap: break-word;
}

.message-wrapper.user .message-bubble {
  border-top-left-radius: 16px;
  border-top-right-radius: 4px;
}

.message-text {
  margin: 0;
  line-height: 1.5;
  font-size: 14px;
  color: #e5e5ea;
  word-break: break-word;
}

/* ========== Markdown 样式修复 ========== */
.message-text h1,
.message-text h2,
.message-text h3,
.message-text h4 {
  margin: 12px 0 8px 0;
  font-weight: 600;
  color: #f0e6d0;
}
.message-text h1 { font-size: 1.5em; }
.message-text h2 { font-size: 1.3em; }
.message-text h3 { font-size: 1.1em; }

.message-text p {
  margin: 8px 0;
}

.message-text ul,
.message-text ol {
  margin: 8px 0;
  padding-left: 20px;
}
.message-text li {
  margin: 4px 0;
  list-style-position: inside;
}
.message-text ol {
  list-style-position: outside;
  padding-left: 28px;
}
.message-text ol li {
  padding-left: 4px;
}
.message-text ul {
  list-style-position: outside;
  padding-left: 24px;
}
.message-text ul li {
  padding-left: 4px;
}

.message-text code {
  background: rgba(0, 0, 0, 0.4);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
  color: #ffd966;
}
.message-text pre {
  background: rgba(0, 0, 0, 0.5);
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 12px 0;
}
.message-text pre code {
  background: transparent;
  padding: 0;
  color: #e5e5ea;
}

.message-text blockquote {
  border-left: 3px solid #c9a87b;
  margin: 8px 0;
  padding-left: 12px;
  color: #cbbd9a;
  font-style: italic;
}

.message-text strong {
  font-weight: bold;
  color: #ffd966;
}
.message-text em {
  font-style: italic;
}

.message-text a {
  color: #42b883;
  text-decoration: none;
}
.message-text a:hover {
  text-decoration: underline;
}

.message-text table {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}
.message-text th,
.message-text td {
  border: 1px solid rgba(255, 255, 255, 0.2);
  padding: 6px 10px;
  text-align: left;
}
.message-text th {
  background: rgba(255, 255, 255, 0.1);
}

.combat-bubble {
  border-left: 3px solid #f59e0b !important;
}

.tool-message-wrapper {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 16px;
  margin: 4px 0;
}
.tool-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(139, 92, 246, 0.3);
  color: #a78bfa;
  letter-spacing: 0.5px;
}
.tool-content {
  margin: 0;
  font-size: 12px;
  font-family: 'Courier New', monospace;
  color: #8e8e93;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(255, 255, 255, 0.03);
  padding: 6px 10px;
  border-radius: 6px;
  max-width: 100%;
  overflow-x: auto;
}

/* ========== 关键词高亮样式（仅保留黄色） ========== */
.message-text :deep(.rpg-keyword-yellow) {
  /* 中世纪风格字体，与导航栏标题一致 */
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'MedievalSharp', 'Germania One', serif;
  font-weight: 600;
  /* 渐变金色文字 */
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  /* 5% 发光效果：模糊半径 5px，颜色为金色半透明 */
  text-shadow: 0 0 5px rgba(184, 138, 68, 0.5);
  letter-spacing: 1px;
}
</style>