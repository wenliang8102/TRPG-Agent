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
.input-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.input-row input {
  flex: 1;
  height: 40px;
  border-radius: 8px;
  border: 1px solid #3f3f3f;
  padding: 0 12px;
}
.input-row button {
  min-width: 96px;
  border: none;
  border-radius: 8px;
  background: #42b883;
  color: #fff;
  cursor: pointer;
}
.input-row button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>