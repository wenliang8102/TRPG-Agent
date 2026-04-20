<template>
  <div class="hp-bar-container" :class="{ dead: isDead }">
    <div class="hp-bar-label">
      <span class="hp-name">{{ name }}</span>
      <span class="hp-numbers">{{ displayOldHp }} → {{ displayNewHp }} / {{ maxHp }}</span>
    </div>
    <div class="hp-bar-track">
      <!-- 伤害闪光层：宽度从旧百分比渐变到 0（或新百分比，可配置） -->
      <div
        class="hp-bar-damage"
        :style="{
          width: damageWidth + '%',
          transition: animateReady ? 'width 1.8s ease-out 0.3s' : 'none'
        }"
      ></div>
      <!-- 实际血条 -->
      <div
        class="hp-bar-fill"
        :class="hpColorClass"
        :style="{
          width: fillWidth + '%',
          transition: animateReady ? 'width 1.8s ease' : 'none'
        }"
      ></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'

const props = defineProps<{
  name: string
  oldHp: number
  newHp: number
  maxHp: number
}>()

// 动画就绪标志
const animateReady = ref(false)

// 内部存储当前血量值
const currentOldHp = ref(props.oldHp)
const currentNewHp = ref(props.newHp)
const currentMaxHp = ref(props.maxHp)

// 伤害层目标宽度（百分比的基础值）：初始为 oldHp，动画启动后设为 0（即完全消失）
const damageTargetHp = ref(props.oldHp)

// 伤害层实际宽度百分比
const damageWidth = computed(() => {
  const baseHp = animateReady.value ? damageTargetHp.value : currentOldHp.value
  return Math.max(0, Math.min(100, (baseHp / currentMaxHp.value) * 100))
})

// 主血条宽度：动画未就绪时显示旧血量，就绪后显示新血量
const fillWidth = computed(() => {
  const baseHp = animateReady.value ? currentNewHp.value : currentOldHp.value
  return Math.max(0, Math.min(100, (baseHp / currentMaxHp.value) * 100))
})

// 标签显示数字（避免动画中闪烁）
const displayOldHp = computed(() => currentOldHp.value)
const displayNewHp = computed(() => currentNewHp.value)

const isDead = computed(() => currentNewHp.value <= 0)

const hpColorClass = computed(() => {
  const ratio = currentNewHp.value / currentMaxHp.value
  if (ratio > 0.5) return 'hp-green'
  if (ratio > 0.25) return 'hp-orange'
  return 'hp-red'
})

// 监听 props 变化（用于同一血条的后续更新）
watch(
  () => [props.oldHp, props.newHp, props.maxHp],
  ([newOld, newNew, newMax]) => {
    // 如果新旧血量相同，不触发动画（可选）
    if (newOld === currentNewHp.value && newNew === currentNewHp.value) return

    // 更新内部值
    currentOldHp.value = newOld
    currentNewHp.value = newNew
    currentMaxHp.value = newMax

    // 如果动画已经就绪，说明是后续更新，需要重置伤害层起点并启动过渡
    if (animateReady.value) {
      // 先将伤害层目标临时设回旧值（保持当前视觉），然后下一帧改为 0
      damageTargetHp.value = newOld
      nextTick(() => {
        damageTargetHp.value = 0
      })
    } else {
      // 首次渲染前的更新，直接同步
      damageTargetHp.value = newOld
    }
  },
  { immediate: true }
)

// 挂载后启动首次动画
onMounted(async () => {
  // 首次渲染时，血条显示旧血量，伤害层显示旧血量，无过渡
  await nextTick()
  // 启动过渡：主血条目标变为 newHp，伤害层目标变为 0
  damageTargetHp.value = 0
  animateReady.value = true
})
</script>

<style scoped>
/* 保持原有样式不变 */
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
}

.hp-bar-fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: 4px;
}

.hp-green { background: #42b883; }
.hp-orange { background: #f59e0b; }
.hp-red { background: #ef4444; }
</style>