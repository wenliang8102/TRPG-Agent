<template>
  <div v-if="pendingAction?.type === 'dice_roll'" class="action-panel">
    <p><strong>动作挂起：判断需要掷骰</strong></p>
    <p>原因：{{ pendingAction.reason }} ({{ pendingAction.formula }})</p>
    <button class="roll-btn" @click="$emit('confirm')" :disabled="disabled">
      确认掷骰
    </button>
  </div>
</template>

<script setup lang="ts">
import type { PendingAction } from '../../Services_/chatService'

defineProps<{
  pendingAction: PendingAction | null
  disabled: boolean
}>()

defineEmits<{
  confirm: []
}>()
</script>

<style scoped>
.action-panel {
  margin-top: 10px;
  padding: 12px;
  background: rgba(255, 165, 0, 0.15);
  border: 1px solid #ffaf40;
  border-radius: 8px;
  text-align: center;
}
.action-panel p {
  margin: 4px 0;
  font-size: 14px;
}
.roll-btn {
  margin-top: 8px;
  padding: 8px 24px;
  background: #ffaf40;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: bold;
}
.roll-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>