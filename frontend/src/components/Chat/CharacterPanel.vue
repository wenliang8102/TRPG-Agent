<!-- frontend/src/components/Chat/CharacterPanel.vue -->
<template>
  <div class="character-panel">
    <!-- 固定头部：标题 + 切换按钮 -->
    <div class="panel-header">
      <h3>{{ viewMode === 'character' ? '角色状态' : '生命值概览' }}</h3>
      <button
        class="view-toggle-btn"
        @click="toggleView"
        :title="viewMode === 'character' ? '切换到血条视图' : '切换到角色详情'"
      >
        <ArrowLeftRight :size="20" stroke-width="1.5" />
      </button>
    </div>

    <!-- 可滚动内容区域 -->
    <div class="panel-scrollable-content">
      <!-- 视图 A：详细角色状态 -->
      <div v-if="viewMode === 'character'">
        <div v-if="player" class="character-stats">
          <!-- 基础信息：名称 + 职业/等级 -->
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

          <!-- 生命值 -->
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

          <!-- 护甲等级 -->
          <div class="stat-row">
            <span class="stat-label">护甲等级</span>
            <span class="stat-value" :class="{ 'ac-changed': acChanged }">
              {{ player.ac }}
            </span>
          </div>

          <!-- 六维属性 -->
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

          <!-- 当前状态 -->
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

          <!-- 资源（法术位等） -->
          <div v-if="player.resources && Object.keys(player.resources).length" class="resources-section">
            <div class="section-title">资源</div>
            <div class="resources-grid">
              <div v-for="(value, key) in player.resources" :key="key" class="resource-item">
                <span class="resource-name">{{ formatResourceName(key) }}</span>
                <span class="resource-value">{{ value }}</span>
              </div>
            </div>
          </div>

          <!-- 道具栏（占位） -->
          <details class="inventory-section">
            <summary class="section-title">道具</summary>
            <div class="inventory-list">
              <div class="empty-state" style="padding: 8px 0;">暂无道具数据</div>
              <!-- 未来若后端提供 player.inventory，可在此处遍历 -->
            </div>
          </details>

          <!-- 法术栏 -->
          <details v-if="player.known_spells?.length" class="spells-section">
            <summary class="section-title">
              法术
              <span v-if="player.spellcasting_ability" class="spell-ability">
                ({{ player.spellcasting_ability.toUpperCase() }})
              </span>
            </summary>
            <div class="spells-list">
              <span v-for="spell in player.known_spells" :key="spell" class="spell-badge">{{ spell }}</span>
            </div>
          </details>
          <div v-else class="spells-section">
            <div class="section-title">法术</div>
            <div class="empty-state" style="padding: 8px 0;">暂无已知法术</div>
          </div>

          <!-- 武器列表 -->
          <details v-if="player.weapons?.length" class="weapons-section">
            <summary class="section-title">武器</summary>
            <div class="weapons-list">
              <div v-for="(w, idx) in player.weapons" :key="idx" class="weapon-item">
                <span class="weapon-name">{{ w.name }}</span>
                <span class="weapon-detail">{{ w.damage_dice }} {{ w.damage_type }}</span>
              </div>
            </div>
          </details>
        </div>

        <div v-else class="empty-state">
          暂无角色数据，开始对话后自动创建。
        </div>
      </div>

      <!-- 视图 B：HP 条视图 -->
      <div v-else class="hp-overview">
        <div v-if="hpUnits.length === 0" class="empty-state">
          暂无单位血量数据
        </div>
        <div v-for="unit in hpUnits" :key="unit.id" class="hp-overview-item">
          <HpBar
            :name="unit.name"
            :old-hp="unit.hp"
            :new-hp="unit.hp"
            :max-hp="unit.max_hp"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import HpBar from './HpBar.vue'
import { ArrowLeftRight } from 'lucide-vue-next'
import { useCharacterState, type PlayerState, ABILITY_LIST, formatConditionName } from '../../Services_/characterStateService'

const props = defineProps<{
  // 外部传入的玩家数据（由父组件同步）
  externalPlayer: PlayerState | null
  combat?: any | null
}>()

// 使用 service 管理角色状态
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

// 监听外部玩家数据变化，同步到 service
watch(() => props.externalPlayer, (newPlayer) => {
  if (newPlayer) {
    updatePlayer(newPlayer)
  }
}, { deep: true, immediate: true })

// 视图切换
const viewMode = ref<'character' | 'hp'>('character')
const toggleView = () => {
  viewMode.value = viewMode.value === 'character' ? 'hp' : 'character'
}

// 资源名称美化（可保留在组件内）
const formatResourceName = (key: string): string => {
  return key
    .replace(/_/g, ' ')
    .replace(/lvl|lv|level/gi, '')
    .replace(/\b\w/g, c => c.toUpperCase())
    .trim() || key
}

// HP 单位列表（用于 HP 视图）
const hpUnits = computed(() => {
  const units: Array<{ id: string; name: string; hp: number; max_hp: number }> = []

  if (player.value) {
    units.push({
      id: `player_${player.value.name}`,
      name: player.value.name || '玩家',
      hp: player.value.hp || 0,
      max_hp: player.value.max_hp || 1,
    })
  }

  if (props.combat?.participants) {
    Object.values(props.combat.participants).forEach((p: any) => {
      if (p.id?.startsWith('player_') && units.some(u => u.id === p.id)) return
      units.push({
        id: p.id,
        name: p.name,
        hp: p.hp,
        max_hp: p.max_hp,
      })
    })
  }

  return units
})
</script>

<style scoped>
/* 样式保持不变（与之前提供的完全一致） */
.character-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0;
  background: rgba(30, 30, 35, 0.8);
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #fff;
  overflow: hidden;
}
.panel-header {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 16px 8px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.panel-header h3 {
  margin: 0;
  font-size: 18px;
}
.view-toggle-btn {
  background: transparent;
  border: 0.5px solid rgba(255, 255, 255, 0.2);
  border-radius: 50%;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #e5e5ea;
  transition: all 0.2s;
}
.view-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.3);
}
.panel-scrollable-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px 16px 16px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.3) transparent;
}
.panel-scrollable-content::-webkit-scrollbar {
  width: 4px;
}
.panel-scrollable-content::-webkit-scrollbar-track {
  background: transparent;
}
.panel-scrollable-content::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 4px;
}
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
  top: 0;
  left: 0;
}
.temp-hp-fill {
  height: 100%;
  background: #3b82f6;
  border-radius: 3px;
  transition: width 0.3s ease;
  position: absolute;
  top: 0;
  left: 0;
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
.spells-section,
.inventory-section {
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
  text-transform: capitalize;
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
.weapons-section {
  margin-top: 8px;
}
.weapons-section summary {
  cursor: pointer;
  list-style: none;
}
.weapons-section summary::-webkit-details-marker {
  display: none;
}
.weapons-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.weapon-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: rgba(0, 0, 0, 0.3);
  padding: 6px 10px;
  border-radius: 8px;
  font-size: 13px;
}
.weapon-name {
  color: #e5e5ea;
  font-weight: 500;
}
.weapon-detail {
  color: #a1a1aa;
  font-family: monospace;
}
/* 动画 */
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
.hp-overview {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>