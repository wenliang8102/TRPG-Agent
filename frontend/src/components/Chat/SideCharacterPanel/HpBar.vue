<template>
  <div class="hpbar-container" :class="{ compact }">
    <div v-if="!compact" class="hpbar-header">
      <span class="hpbar-name">{{ name }}</span>
      <span class="hpbar-value">{{ displayedHp }} / {{ maxHp }}</span>
    </div>

    <div class="hpbar-track">
      <div
        class="hpbar-fill old-fill"
        :style="{ width: oldPercent + '%' }"
      ></div>
      <div
        class="hpbar-fill new-fill"
        :class="fillClass"
        :style="{ width: newPercent + '%' }"
      ></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'

const props = defineProps<{
  name: string
  oldHp: number
  newHp: number
  maxHp: number
  compact?: boolean
}>()

const displayedHp = ref(props.oldHp)
const oldPercent = ref((props.oldHp / props.maxHp) * 100)
const newPercent = ref((props.oldHp / props.maxHp) * 100)

const fillClass = computed(() => {
  if (props.newHp < props.oldHp) return 'damage'
  if (props.newHp > props.oldHp) return 'heal'
  return ''
})

watch(
  () => [props.oldHp, props.newHp, props.maxHp],
  async ([oldHp, newHp, maxHp]) => {
    oldPercent.value = (oldHp / maxHp) * 100
    newPercent.value = (oldHp / maxHp) * 100
    displayedHp.value = oldHp

    await new Promise(resolve => setTimeout(resolve, 50))

    newPercent.value = (newHp / maxHp) * 100
    displayedHp.value = newHp
  },
  { immediate: true }
)
</script>

<style scoped>
.hpbar-container {
  width: 100%;
  margin-bottom: 12px;
}

.hpbar-container.compact {
  margin-bottom: 0;
}

.hpbar-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
  font-size: 14px;
  color: #e5e5ea;
}

.hpbar-name {
  font-weight: 600;
}

.hpbar-value {
  font-family: monospace;
  color: #a1a1aa;
}

.hpbar-track {
  position: relative;
  width: 100%;
  height: 18px;
  background: linear-gradient(180deg, #121212 0%, #090909 100%);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: inset 0 0 6px rgba(0, 0, 0, 0.68);
}

.hpbar-fill {
  position: absolute;
  left: 0;
  top: 0;
  height: 100%;
  transition: width 0.6s ease;
  border-radius: 10px;
}

.old-fill {
  background: rgba(255, 255, 255, 0.15);
  z-index: 1;
}

.new-fill {
  z-index: 2;
}

.new-fill.damage {
  background: linear-gradient(90deg, #ef4444, #b91c1c);
}

.new-fill.heal {
  background: linear-gradient(90deg, #22c55e, #15803d);
}

.new-fill:not(.damage):not(.heal) {
  background: linear-gradient(90deg, #42b883, #2f855a);
}
</style>
