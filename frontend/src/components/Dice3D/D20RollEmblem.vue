<template>
  <div class="d20-emblem" aria-label="D20 roll result">
    <img class="d20-image" :src="d20Image" alt="" />
    <span class="result-number" :class="{ single: displayValue < 10 }">{{ displayValue }}</span>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import d20Image from '../../assets/d20-blue-gold-cutout.png'

const props = defineProps<{
  value: number
}>()

const displayValue = ref(props.value)
let timer: ReturnType<typeof window.setTimeout> | null = null
let stopTimer: ReturnType<typeof window.setTimeout> | null = null

// 固定图像徽章，掷骰感来自数字高速跳变后停在最终值。
onMounted(() => {
  const tick = () => {
    displayValue.value = Math.floor(Math.random() * 20) + 1
    timer = window.setTimeout(tick, 16 + Math.floor(Math.random() * 9))
  }
  tick()

  stopTimer = window.setTimeout(() => {
    if (timer) {
      window.clearTimeout(timer)
      timer = null
    }
    displayValue.value = props.value
  }, 980)
})

onUnmounted(() => {
  if (timer) window.clearTimeout(timer)
  if (stopTimer) window.clearTimeout(stopTimer)
})
</script>

<style scoped>
.d20-emblem {
  position: relative;
  width: min(270px, 64vw);
  aspect-ratio: 1.3;
  margin: 0 auto;
}

.d20-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
  user-select: none;
  pointer-events: none;
}

.result-number {
  position: absolute;
  left: 50%;
  top: 48%;
  transform: translate(-50%, -50%);
  min-width: 1.6em;
  font-family: 'Cormorant Garamond', 'Georgia', 'Times New Roman', serif;
  font-size: 44px;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  letter-spacing: -1px;
  background: linear-gradient(180deg, #fff8c2 0%, #ffe36b 62%, #d9ad45 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-stroke: 0.35px rgba(76, 47, 10, 0.3);
  text-shadow: 0 0 6px rgba(255, 226, 107, 0.45), 0 1px 3px rgba(0, 0, 0, 0.38);
  transform: translate(-50%, -50%) scaleX(0.84);
}

.result-number.single {
  transform: translate(-50%, -50%) scaleX(0.82);
}

@media (max-width: 720px) {
  .d20-emblem {
    width: min(240px, 72vw);
  }

  .result-number {
    font-size: 40px;
  }
}
</style>
