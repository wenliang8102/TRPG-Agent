<!-- frontend/src/Pages_/DiceAnimationPage.vue -->
<template>
  <div class="dice-animation-overlay">
    <!-- 全屏3D骰子 -->
    <Dice3D ref="dice3dRef" />
    
    <!-- 数字显示浮层 -->
    <div class="result-overlay" :class="{ show: showResult }">
      <div class="number-label">✦ FATE SEALED ✦</div>
      <div class="number-value" :class="{ rolling: isRolling }">
        {{ displayValue }}
      </div>
      <div class="number-sub">DARK SOUL DICE</div>
    </div>
    
    <p class="hint-text" :class="{ hidden: showResult }">
      {{ isRolling ? '⚔️ 命运之骰正在抛出... ⚔️' : '' }}
    </p>
    
    <button class="close-btn" @click="closeAnimation">✕</button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import Dice3D from '../components/Dice3D/Dice3D.vue'

const emit = defineEmits<{
  close: []
}>()

const dice3dRef = ref<InstanceType<typeof Dice3D> | null>(null)
const isRolling = ref(true)
const showResult = ref(false)
const displayValue = ref('?')
let closeTimer: ReturnType<typeof setTimeout> | null = null

const performRoll = async () => {
  try {
    const finalNumber = await dice3dRef.value?.throwDice()
    displayValue.value = String(finalNumber || Math.floor(Math.random() * 20) + 1)
  } catch (error) {
    console.error('Roll error:', error)
    displayValue.value = String(Math.floor(Math.random() * 20) + 1)
  } finally {
    isRolling.value = false
    showResult.value = true
    
    // 3秒后自动关闭
    closeTimer = setTimeout(() => {
      closeAnimation()
    }, 3000)
  }
}

const closeAnimation = () => {
  // 清理定时器
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
  emit('close')
}

onMounted(() => {
  performRoll()
})

onBeforeUnmount(() => {
  // 组件卸载前清理定时器
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
})
</script>

<style scoped>
.dice-animation-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 2000;
  background: radial-gradient(ellipse at center, #0a0508 0%, #020103 100%);
  pointer-events: auto;
}

.result-overlay {
  position: absolute;
  bottom: 15%;
  left: 0;
  right: 0;
  text-align: center;
  opacity: 0;
  transform: translateY(30px);
  transition: all 0.6s ease-out;
  pointer-events: none;
  z-index: 10;
}

.result-overlay.show {
  opacity: 1;
  transform: translateY(0);
}

.number-label {
  font-family: 'Cinzel', 'Georgia', serif;
  font-size: 14px;
  letter-spacing: 8px;
  color: #8a6a4a;
  text-shadow: 0 0 15px rgba(180, 120, 70, 0.5);
}

.number-value {
  font-family: 'Cinzel', 'Georgia', serif;
  font-size: 120px;
  font-weight: bold;
  color: #d4a060;
  text-shadow: 0 0 50px rgba(180, 120, 70, 0.6);
  margin: 10px 0;
  line-height: 1;
}

.number-value.rolling {
  animation: fatePulse 0.3s ease-in-out infinite;
}

@keyframes fatePulse {
  0%, 100% { opacity: 1; text-shadow: 0 0 20px #d4a060; transform: scale(1); }
  50% { opacity: 0.6; text-shadow: 0 0 60px #ffcc80; transform: scale(1.05); }
}

.number-sub {
  font-family: 'Cinzel', 'Georgia', serif;
  font-size: 11px;
  letter-spacing: 5px;
  color: #5a4a3a;
}

.hint-text {
  position: absolute;
  bottom: 8%;
  left: 0;
  right: 0;
  text-align: center;
  font-family: 'Cinzel', 'Georgia', serif;
  font-size: 12px;
  letter-spacing: 3px;
  color: #6a5a4a;
  text-shadow: 0 0 5px rgba(0, 0, 0, 0.5);
  transition: opacity 0.5s;
}

.hint-text.hidden {
  opacity: 0;
}

.close-btn {
  position: absolute;
  top: 30px;
  right: 30px;
  width: 48px;
  height: 48px;
  background: rgba(180, 120, 70, 0.15);
  border: 1px solid rgba(180, 120, 70, 0.4);
  border-radius: 50%;
  color: #d4a060;
  font-size: 22px;
  cursor: pointer;
  transition: all 0.3s;
  backdrop-filter: blur(8px);
  z-index: 20;
}

.close-btn:hover {
  background: rgba(180, 120, 70, 0.35);
  border-color: #d4a060;
  transform: rotate(90deg) scale(1.05);
  box-shadow: 0 0 20px rgba(180, 120, 70, 0.4);
}
</style>