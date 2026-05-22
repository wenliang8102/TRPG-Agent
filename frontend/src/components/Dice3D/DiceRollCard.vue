<template>
  <div class="dice-roll-wrapper">
    <section class="dice-roll-card" :class="outcomeClass">
      <div class="dice-stage">
        <D20RollEmblem :value="roll.raw_roll" />
      </div>

      <div class="roll-result">
        <p class="roll-total">{{ totalFormula }}</p>
        <p v-if="hasTarget" class="roll-outcome">{{ outcomeText }}</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import D20RollEmblem from './D20RollEmblem.vue'
import type { DiceRollEvent } from '../../Services_/chatService'

const props = defineProps<{
  roll: DiceRollEvent
}>()

const modifier = computed(() => props.roll.modifier ?? props.roll.final_total - props.roll.raw_roll)
const hasTarget = computed(() => typeof props.roll.target === 'number')
const totalFormula = computed(() => {
  const sign = modifier.value >= 0 ? '+' : '-'
  return `${props.roll.raw_roll} ${sign} ${Math.abs(modifier.value)} = ${props.roll.final_total}`
})
const isSuccess = computed(() => hasTarget.value && props.roll.final_total >= Number(props.roll.target))
const outcomeClass = computed(() => {
  if (!hasTarget.value) return 'neutral'
  return isSuccess.value ? 'success' : 'failure'
})
const outcomeText = computed(() => isSuccess.value ? 'SUCCESS' : 'FAILURE')

</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=UnifrakturMaguntia&family=MedievalSharp&family=Germania+One&display=swap');

.dice-roll-wrapper {
  padding: 16px 0;
  animation: fadeInUp 0.3s ease-out;
}

.dice-roll-card {
  position: relative;
  overflow: hidden;
  min-height: 330px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  border: 1px solid rgba(196, 146, 58, 0.85);
  border-radius: 8px;
  background:
    radial-gradient(circle at 50% 42%, rgba(44, 70, 200, 0.22), transparent 34%),
    linear-gradient(135deg, rgba(24, 20, 60, 0.98), rgba(6, 15, 38, 0.98));
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.42), inset 0 0 0 1px rgba(245, 210, 120, 0.16);
  color: #f8edc0;
  font-family: 'Cinzel', 'Georgia', serif;
}

.dice-stage {
  position: relative;
  z-index: 1;
  width: min(390px, 72vw);
  height: 225px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}

.roll-result {
  position: relative;
  z-index: 2;
  margin-top: 8px;
}

.roll-total {
  margin: 0;
  color: #f4df9a;
  font-size: 18px;
  font-weight: 800;
  text-align: center;
  letter-spacing: 0.04em;
}

.roll-outcome {
  margin: 14px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'MedievalSharp', 'Germania One', serif;
  font-size: 34px;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  opacity: 0;
  transform: translateY(14px);
  animation: outcomeReveal 0.9s cubic-bezier(0.22, 1, 0.36, 1) 0.18s forwards;
  text-shadow:
    0 0 10px rgba(0, 0, 0, 0.34),
    0 0 22px rgba(255, 227, 154, 0.12);
}

.roll-outcome::before,
.roll-outcome::after {
  content: none;
}

.success .roll-outcome {
  color: #e5d4a8;
  text-shadow:
    0 0 10px rgba(0, 0, 0, 0.42),
    0 0 18px rgba(184, 138, 68, 0.18),
    0 0 30px rgba(230, 213, 168, 0.1);
}

.success .roll-outcome::before,
.success .roll-outcome::after {
  color: #f6d978;
}

.failure .roll-outcome {
  color: #5b1717;
  text-shadow:
    0 0 10px rgba(18, 0, 0, 0.55),
    0 0 16px rgba(68, 8, 8, 0.22),
    0 0 28px rgba(122, 18, 18, 0.08);
}

.failure .roll-outcome::before,
.failure .roll-outcome::after {
  color: #7a2020;
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

@keyframes outcomeReveal {
  from {
    opacity: 0;
    transform: translateY(14px);
    filter: blur(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
    filter: blur(0);
  }
}

@media (max-width: 720px) {
  .dice-roll-card {
    min-height: 300px;
  }

  .dice-stage {
    width: min(320px, 82vw);
    height: 210px;
  }

  .roll-total {
    font-size: 16px;
  }

  .roll-outcome {
    gap: 9px;
    font-size: 28px;
    letter-spacing: 0.12em;
  }
}
</style>
