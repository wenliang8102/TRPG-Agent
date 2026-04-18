<!--

<template>
  <div class="combat-panel">
    <h3>角色栏</h3>

    <div v-if="!combat || !combat.participants" class="empty-state">
      目前没有参与的角色
    </div>

    <div v-else>
      <div class="round-info">
        当前回合 {{ combat.round }} | 角色: {{ combat.current_actor_id }}
      </div>

      <div class="participants-list">
        <div 
          v-for="p in aliveParticipants" 
          :key="p.id" 
          class="participant-card"
          :class="{ active: p.id === combat.current_actor_id }"
        >
          <div class="p-header">
            <strong>{{ p.name }} <span class="p-id">({{ p.id }})</span></strong>
            <span class="p-hp">{{ p.hp }} / {{ p.max_hp }} HP</span>
          </div>
          <div class="p-ac">AC: {{ p.ac }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"

const props = defineProps<{
  combat: any | null
}>()

const aliveParticipants = computed<any[]>(() => {
  if (!props.combat || !props.combat.participants) return []
  return Object.values(props.combat.participants).filter((p: any) => p.hp > 0) as any[]
})
</script>

<style scoped>
.combat-panel {
  padding: 16px;
  background: rgba(30, 30, 35, 0.8);
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #fff;
  height: 100%;
  overflow-y: auto;
}

h3 {
  margin-top: 0;
  margin-bottom: 16px;
  font-size: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 8px;
}

.empty-state {
  color: #8e8e93;
  font-style: italic;
}

.round-info {
  margin-bottom: 12px;
  font-weight: bold;
  color: #42b883;
}

.participants-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.participant-card {
  padding: 10px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  border-left: 3px solid transparent;
  transition: all 0.2s;
}

.participant-card.active {
  border-left-color: #42b883;
  background: rgba(66, 184, 131, 0.1);
}

.p-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
}

.p-id {
  font-size: 12px;
  color: #8e8e93;
}

.p-hp {
  font-weight: bold;
}

.p-ac {
  font-size: 13px;
  color: #a1a1aa;
}
</style>

-->