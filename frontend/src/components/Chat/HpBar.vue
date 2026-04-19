<template>
  <div class="hp-bar-container" :class="{ dead: isDead }">
    <div class="hp-bar-label">
      <span class="hp-name">{{ name }}</span>
      <span class="hp-numbers">{{ oldHp }} → {{ newHp }} / {{ maxHp }}</span>
    </div>
    <div class="hp-bar-track">
      <!-- 伤害闪光层：从旧血量过渡到新血量 -->
      <div class="hp-bar-damage" :style="{ width: oldPercent + '%' }"></div>
      <!-- 实际血条 -->
      <div class="hp-bar-fill" :class="hpColorClass" :style="{ width: newPercent + '%' }"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'

const props = defineProps<{
  name: string
  oldHp: number
  newHp: number
  maxHp: number
}>()

// 内部保存上一次渲染时的血量，用于计算动画起点
const previousHp = ref(props.oldHp)
const currentNewHp = ref(props.newHp)
const currentMaxHp = ref(props.maxHp)

// 监听 props 变化，更新内部状态并触发过渡
watch(() => [props.oldHp, props.newHp, props.maxHp], ([newOld, newNew, newMax]) => {
  // 只有当新旧血量确实不同时才更新 previousHp，保留动画起始点
  if (newOld !== currentNewHp.value) {
    previousHp.value = currentNewHp.value
  }
  currentNewHp.value = newNew
  currentMaxHp.value = newMax
}, { immediate: true })

const isDead = computed(() => currentNewHp.value <= 0)
const oldPercent = computed(() => Math.max(0, Math.min(100, (previousHp.value / currentMaxHp.value) * 100)))
const newPercent = computed(() => Math.max(0, Math.min(100, (currentNewHp.value / currentMaxHp.value) * 100)))

const hpColorClass = computed(() => {
  const ratio = currentNewHp.value / currentMaxHp.value
  if (ratio > 0.5) return 'hp-green'
  if (ratio > 0.25) return 'hp-orange'
  return 'hp-red'
})
</script>
<style scoped>
.hp-bar-container {
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  margin-top: 6px;
}

.hp-bar-container.dead {
  opacity: 0.5;
}

.hp-bar-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 4px;
  color: #d4d4d8;
}

.hp-name {
  font-weight: 600;
}

.hp-numbers {
  font-family: 'Courier New', monospace;
  color: #a1a1aa;
}

.hp-bar-track {
  position: relative;
  height: 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.hp-bar-damage {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: rgba(239, 68, 68, 0.4);
  border-radius: 4px;
   transition: width 1.8s ease-out 0.3s;  /* 可选，与主条同步 */
}

.hp-bar-fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: 4px;
   transition: width 1.8s ease;  /* 🔥 修改为 1.8 秒 */
}

.hp-green { background: #42b883; }
.hp-orange { background: #f59e0b; }
.hp-red { background: #ef4444; }
</style>
