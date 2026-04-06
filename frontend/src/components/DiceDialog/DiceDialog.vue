<template>
  <Teleport to="body">
    <div v-if="visible" class="dialog-overlay" @click.self="close">
      <div class="dialog-content">
        <h3>🎲 掷骰子</h3>
        <div class="dice-options">
          <button v-for="dice in dices" :key="dice" @click="selectDice(dice)">
            d{{ dice }}
          </button>
        </div>
        <p v-if="result">结果：{{ result }}</p>
        <button @click="roll">掷骰</button>
        <button class="close-btn" @click="close">关闭</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const dices = [4, 6, 8, 10, 12, 20]
const selectedDice = ref(20)
const result = ref<number | null>(null)

const selectDice = (dice: number) => {
  selectedDice.value = dice
}

const roll = () => {
  result.value = Math.floor(Math.random() * selectedDice.value) + 1
}

const close = () => {
  emit('close')
  result.value = null
}
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.dialog-content {
  background: #1a1a1a;
  border: 1px solid #3f3f3f;
  border-radius: 12px;
  padding: 24px;
  min-width: 280px;
  text-align: center;
}
.dice-options {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin: 16px 0;
}
.dice-options button {
  padding: 6px 12px;
  border: 1px solid #3f3f3f;
  background: #2a2a2a;
  border-radius: 6px;
  cursor: pointer;
}
.dice-options button:hover {
  background: #3a3a3a;
}
.close-btn {
  margin-left: 8px;
  background: #3f3f3f;
}
button {
  margin-top: 12px;
  padding: 8px 20px;
  border-radius: 6px;
  border: none;
  cursor: pointer;
}
</style>