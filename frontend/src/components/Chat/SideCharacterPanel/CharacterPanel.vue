<!-- frontend/src/components/Chat/SideCharacterPanel/CharacterPanel.vue -->
<template>
  <div v-if="player" class="character-stats">
    <!-- 基础信息保留在角色页，避免和其他侧栏模式互相干扰 -->
    <div class="stat-row">
      <span class="stat-label">名称</span>
      <span class="stat-value">{{ player.name || '无名冒险者' }}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">职业</span>
      <span class="stat-value">
        {{ player.role_class || '冒险者' }}
        <span v-if="player.level"> Lv.{{ player.level }}</span>
      </span>
    </div>

    <div class="stat-row">
      <span class="stat-label">生命值</span>
      <div class="hp-display">
        <span class="stat-value" :class="{ 'hp-changed': hpChanged }">
          {{ player.hp }} / {{ player.max_hp }}
          <span v-if="player.temp_hp" class="temp-hp"> (+{{ player.temp_hp }} 临时)</span>
        </span>
        <div class="hp-bar">
          <div class="hp-fill" :style="{ width: hpPercent + '%' }"></div>
          <div v-if="player.temp_hp" class="temp-hp-fill" :style="{ width: tempHpPercent + '%' }"></div>
        </div>
      </div>
    </div>

    <div class="stat-row">
      <span class="stat-label">护甲等级</span>
      <span class="stat-value" :class="{ 'ac-changed': acChanged }">
        {{ player.ac }}
      </span>
    </div>

    <div class="abilities-section">
      <div class="section-title">属性值</div>
      <div class="abilities-grid">
        <div v-for="ability in ABILITY_LIST" :key="ability.key" class="ability-item">
          <span class="ability-name">{{ ability.label }}</span>
          <span
            class="ability-score"
            :class="{ 'ability-changed': abilityChanged[ability.key] }"
          >
            {{ player.abilities?.[ability.key] ?? '—' }}
          </span>
          <span
            v-if="showModifier[ability.key]"
            class="ability-modifier"
            :class="{ 'modifier-changed': modifierChanged[ability.key] }"
          >
            {{ getModifierDisplay(ability.key) }}
          </span>
        </div>
      </div>
    </div>

    <div v-if="typedConditions.length" class="conditions-section">
      <div class="section-title">当前状态</div>
      <div class="conditions-list">
        <span
          v-for="cond in typedConditions"
          :key="String(cond.id)"
          class="condition-badge"
          :title="`来源: ${cond.source_id || '未知'} | 剩余: ${cond.duration ?? '永久'}`"
        >
          {{ formatConditionName(cond.id) }}
          <span v-if="cond.duration" class="duration-badge">{{ cond.duration }}</span>
        </span>
      </div>
    </div>

    <div v-if="player.resources && Object.keys(player.resources).length" class="resources-section">
      <div class="section-title">资源</div>
      <div class="resources-grid">
        <div v-for="(value, key) in player.resources" :key="key" class="resource-item">
          <span class="resource-name">{{ translateResourceName(key) }}</span>
          <span class="resource-value">{{ value }}</span>
        </div>
      </div>
    </div>

    <details v-if="player.known_spells?.length" class="spells-section">
      <summary class="section-title">
        法术
        <span v-if="player.spellcasting_ability" class="spell-ability">
          ({{ player.spellcasting_ability.toUpperCase() }})
        </span>
      </summary>
      <div class="spells-list">
        <span v-for="spell in player.known_spells" :key="spell" class="spell-badge">
          {{ translateSpellName(spell) }}
        </span>
      </div>
    </details>
    <div v-else class="spells-section">
      <div class="section-title">法术</div>
      <div class="empty-state empty-state-compact">暂无已知法术</div>
    </div>

  </div>

  <div v-else class="empty-state">
    暂无角色数据，开始对话后自动创建。
  </div>
</template>

<script setup lang="ts">
import { watch } from 'vue'
import { useCharacterState, type PlayerState, ABILITY_LIST, formatConditionName } from '../../../Services_/characterStateService'
import { translateSpellName, translateResourceName } from '../../../Services_/nameTranslator'

const props = defineProps<{
  externalPlayer: PlayerState | null
}>()

const {
  player,
  hpChanged,
  acChanged,
  abilityChanged,
  showModifier,
  modifierChanged,
  hpPercent,
  tempHpPercent,
  typedConditions,
  updatePlayer,
  getModifierDisplay,
} = useCharacterState(props.externalPlayer)

watch(() => props.externalPlayer, (newPlayer) => {
  if (newPlayer) {
    updatePlayer(newPlayer)
  }
}, { deep: true, immediate: true })
</script>

<style scoped>
.character-stats {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 4px;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.stat-label {
  font-size: 14px;
  color: #a1a1aa;
}

.stat-value {
  font-size: 16px;
  font-weight: 600;
  color: #e5e5ea;
}

.hp-display {
  flex: 1;
  text-align: right;
}

.temp-hp {
  font-size: 12px;
  color: #3b82f6;
}

.hp-bar {
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
  margin-top: 6px;
  overflow: hidden;
  position: relative;
}

.hp-fill {
  height: 100%;
  background: #42b883;
  border-radius: 3px;
  transition: width 0.3s ease;
  position: absolute;
  inset: 0 auto 0 0;
}

.temp-hp-fill {
  height: 100%;
  background: #3b82f6;
  border-radius: 3px;
  transition: width 0.3s ease;
  position: absolute;
  inset: 0 auto 0 0;
  opacity: 0.6;
}

.conditions-section {
  margin: 8px 0;
  padding: 8px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.conditions-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.condition-badge {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  border: 0.5px solid rgba(239, 68, 68, 0.3);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.duration-badge {
  background: rgba(0, 0, 0, 0.3);
  border-radius: 10px;
  padding: 0 4px;
  font-size: 10px;
  color: #cbd5e1;
}

.abilities-section,
.resources-section,
.spells-section {
  margin: 8px 0;
  padding: 8px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #c9a87b;
  margin-bottom: 12px;
  letter-spacing: 1px;
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

details summary {
  list-style: none;
}

details summary::-webkit-details-marker {
  display: none;
}

.spell-ability {
  font-size: 12px;
  font-weight: normal;
  color: #a1a1aa;
}

.abilities-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.ability-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  background: rgba(0, 0, 0, 0.3);
  padding: 8px 4px;
  border-radius: 12px;
  transition: all 0.2s;
}

.ability-name {
  font-size: 12px;
  color: #a1a1aa;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.ability-score {
  font-size: 20px;
  font-weight: 700;
  color: #e6d5b8;
  line-height: 1;
}

.ability-modifier {
  font-size: 12px;
  color: #42b883;
  font-weight: 500;
}

.resources-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.resource-item {
  background: rgba(0, 0, 0, 0.3);
  padding: 6px 10px;
  border-radius: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.resource-name {
  font-size: 13px;
  color: #a1a1aa;
}

.resource-value {
  font-size: 16px;
  font-weight: 600;
  color: #e6d5b8;
}

.spells-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.spell-badge {
  background: rgba(139, 92, 246, 0.2);
  color: #c4b5fd;
  padding: 4px 10px;
  border-radius: 16px;
  font-size: 12px;
  border: 0.5px solid rgba(139, 92, 246, 0.3);
}

.hp-changed {
  animation: hpFlash 0.6s ease;
}

.ac-changed {
  animation: acFlash 0.6s ease;
}

.ability-changed {
  animation: abilityFlash 0.5s ease;
}

@keyframes hpFlash {
  0% { color: #ef4444; text-shadow: 0 0 4px #ef4444; }
  100% { color: #e5e5ea; text-shadow: none; }
}

@keyframes acFlash {
  0% { color: #f59e0b; text-shadow: 0 0 4px #f59e0b; }
  100% { color: #e5e5ea; text-shadow: none; }
}

@keyframes abilityFlash {
  0% { color: #facc15; text-shadow: 0 0 6px #facc15; transform: scale(1.1); }
  100% { color: #e6d5b8; text-shadow: none; transform: scale(1); }
}

.empty-state {
  color: #8e8e93;
  font-style: italic;
  text-align: center;
  padding: 20px 0;
}

.empty-state-compact {
  padding: 8px 0;
}
</style>
