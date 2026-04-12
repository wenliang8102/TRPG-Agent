<template>
  <form class="input-row" @submit.prevent="onSubmit">
    <input
      v-model="text"
      type="text"
      :placeholder="placeholder"
      :disabled="disabled"
    />
    <button type="submit" :disabled="disabled || !text.trim()">
      {{ buttonText }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled: boolean
  buttonText?: string
  placeholder?: string
}>()

const emit = defineEmits<{
  send: [text: string]
}>()

const text = ref('')

const onSubmit = () => {
  const content = text.value.trim()
  if (!content) return
  emit('send', content)
  text.value = ''
}
</script>

<style scoped>
/* 引入中世纪风格字体（与 Sidebar 一致） */
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=UnifrakturMaguntia&display=swap');

.input-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
  backdrop-filter: blur(16px);
  background: rgba(20, 20, 25, 0.5);
  border-radius: 12px;
  padding: 6px;
  border: 0.5px solid rgba(255, 255, 255, 0.1);
}

.input-row input {
  flex: 1;
  height: 40px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.3);
  border: 0.5px solid rgba(255, 255, 255, 0.15);
  padding: 0 12px;
  color: #e5e5ea;
  font-family: 'Cinzel', serif;
  font-size: 14px;
  transition: all 0.2s;
}

.input-row input:focus {
  outline: none;
  border-color: #42b883;
  box-shadow: 0 0 8px rgba(66, 184, 131, 0.3);
  background: rgba(0, 0, 0, 0.5);
}

.input-row input::placeholder {
  color: #8e8e93;
  font-family: 'Cinzel', serif;
  font-size: 12px;
}

/* 按钮样式：无色半透明背景 + 中世纪渐变金色文字 */
.input-row button {
  min-width: 96px;
  border: none;
  border-radius: 8px;
  background: transparent;          /* 完全透明背景 */
  backdrop-filter: blur(8px);       /* 毛玻璃效果，实现半透明视觉 */
  cursor: pointer;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'MedievalSharp', 'Germania One', serif;
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  transition: all 0.2s;
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  /* 渐变金色文字（与导航栏标题一致） */
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  /* 无发光效果 */
}

.input-row button:hover:not(:disabled) {
  background-color: rgba(255, 255, 255, 0.1); /* 只改变背景颜色，不覆盖渐变 */
  backdrop-filter: blur(8px);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
  border-color: rgba(255, 255, 255, 0.3);
  /* 悬浮时文字轻微发光（可选） */
  text-shadow: 0 0 3px rgba(184, 138, 68, 0.6);
}

.input-row button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>