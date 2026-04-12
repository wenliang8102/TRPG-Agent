<template>
  <Teleport to="body">
    <div v-if="pendingAction" class="action-modal-overlay">
      <div class="action-modal-container">
        <div class="action-modal-content">
          <!-- 掷骰确认面板 -->
          <div v-if="pendingAction.type === 'dice_roll'" class="action-panel dice-panel">
            <h2>动作挂起</h2>
            <p class="reason">原因：{{ pendingAction.reason }}</p>
            <p class="formula">掷骰公式：{{ pendingAction.formula }}</p>
            <button class="confirm-btn" @click="$emit('confirm')" :disabled="disabled">
              确认掷骰
            </button>
          </div>

          <!-- 玩家死亡面板 -->
          <div v-else-if="pendingAction.type === 'player_death'" class="action-panel death-panel">
            <h2>角色倒下</h2>
            <p class="death-summary" v-if="pendingAction.summary">{{ pendingAction.summary }}</p>
            <div class="death-buttons">
              <button class="revive-btn" @click="$emit('revive')" :disabled="disabled">
                复活继续
              </button>
              <button class="end-btn" @click="$emit('endCombat')" :disabled="disabled">
                结束战斗
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import type { PendingAction } from '../../Services_/chatService'

defineProps<{
  pendingAction: PendingAction | null
  disabled: boolean
}>()

defineEmits<{
  confirm: []
  revive: []
  endCombat: []
}>()
</script>

<style scoped>
.action-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.action-modal-container {
  width: 90%;
  max-width: 500px;
  background: rgba(30, 30, 35, 0.95);
  backdrop-filter: blur(20px);
  border-radius: 20px;
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  overflow: hidden;
  animation: scaleIn 0.2s cubic-bezier(0.2, 0.9, 0.4, 1.1);
}

@keyframes scaleIn {
  from {
    opacity: 0;
    transform: scale(0.95);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.action-modal-content {
  padding: 24px 20px 32px;
}

.action-panel {
  text-align: center;
}
.action-panel h2 {
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 20px;
  color: #e5e5ea;
  letter-spacing: 1px;
}
.reason, .formula, .death-summary {
  color: #a1a1aa;
  font-size: 14px;
  margin: 12px 0;
  background: rgba(0, 0, 0, 0.3);
  padding: 12px;
  border-radius: 12px;
  text-align: left;
}
.death-summary {
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.confirm-btn, .revive-btn, .end-btn {
  padding: 10px 24px;
  font-size: 16px;
  font-weight: 600;
  border: none;
  border-radius: 40px;
  cursor: pointer;
  transition: all 0.2s;
  margin: 12px 8px;
  min-width: 140px;
  background: rgba(66, 184, 131, 0.15);
  color: #42b883;
  border: 0.5px solid rgba(66, 184, 131, 0.3);
}
.confirm-btn:hover:not(:disabled), 
.revive-btn:hover:not(:disabled), 
.end-btn:hover:not(:disabled) {
  background: rgba(66, 184, 131, 0.3);
  transform: translateY(-2px);
}
.confirm-btn:active, .revive-btn:active, .end-btn:active {
  transform: translateY(0);
}
.confirm-btn:disabled, .revive-btn:disabled, .end-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.death-panel h2 {
  color: #ef4444;
}
.death-panel .death-summary {
  border-left: 2px solid #ef4444;
}
.death-buttons {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .action-modal-container {
    width: 92%;
  }
  .confirm-btn, .revive-btn, .end-btn {
    min-width: 120px;
    padding: 8px 16px;
    font-size: 14px;
  }
}
</style>