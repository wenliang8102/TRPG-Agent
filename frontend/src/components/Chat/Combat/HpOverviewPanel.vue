<template>
  <div class="hp-overview">
    <div v-if="hpUnits.length === 0" class="empty-state">
      暂无单位血量数据
    </div>

    <template v-else>
      <div v-if="combatActive" class="overview-banner">
        <div class="banner-copy">
          <span class="banner-label">先攻顺序</span>
          <strong>{{ activeActorLabel }}</strong>
        </div>
        <div class="banner-round">第 {{ combatRound }} 轮</div>
      </div>

      <TransitionGroup name="initiative-list" tag="div" class="hp-unit-list">
        <button
          v-for="unit in hpUnits"
          :key="unit.id"
          type="button"
          class="hp-overview-item"
          :class="{
            'is-active': unit.isCurrentActor,
            'is-enemy-turn': unit.isCurrentActor && unit.side === 'enemy',
            'is-player-turn': unit.isCurrentActor && unit.side !== 'enemy',
            'is-player-unit': unit.isPlayerUnit,
            'is-clickable': canOpenControlledActions && unit.isCurrentActor && unit.side !== 'enemy',
          }"
          :disabled="!combatActive || !unit.isCurrentActor || unit.side === 'enemy'"
          @click="handleUnitClick(unit)"
        >
          <div class="item-meta">
            <div class="meta-main">
              <span class="unit-name">{{ unit.name }}</span>
              <span v-if="unit.isCurrentActor" class="turn-badge">行动中</span>
              <span v-else-if="unit.initiativeLabel" class="initiative-badge">
                {{ unit.initiativeLabel }}
              </span>
            </div>
            <div class="meta-side">
              <span class="side-badge" :class="`side-${unit.side}`">{{ unit.sideLabel }}</span>
              <span class="hp-value">{{ unit.hp }} / {{ unit.maxHp }}</span>
            </div>
          </div>

          <HpBar
            :name="unit.name"
            :old-hp="unit.hp"
            :new-hp="unit.hp"
            :max-hp="unit.maxHp"
            compact
          />
        </button>
      </TransitionGroup>
    </template>

  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import HpBar from '../SideCharacterPanel/HpBar.vue'
import type { AvailabilitySelectionUnit } from '../../../Services_/actionAvailabilityService'
import type { PlayerState } from '../../../Services_/characterStateService'

type HpOverviewUnit = {
  id: string
  name: string
  hp: number
  maxHp: number
  side: string
  sideLabel: string
  initiative: number
  initiativeRank: number | null
  initiativeLabel: string
  isCurrentActor: boolean
  isPlayerUnit: boolean
}

type CombatTargetOption = {
  id: string
  name: string
  side: string
  hp: number
  maxHp: number
}

const props = defineProps<{
  externalPlayer: PlayerState | null
  combat?: Record<string, any> | null
  space?: Record<string, any> | null
  sceneUnits?: Record<string, any> | null
  selectedUnit?: AvailabilitySelectionUnit | null
  actionSheetRequestId?: number
  requestCombatActionPanel?: ((preferredTarget?: AvailabilitySelectionUnit | null) => void) | null
}>()

const previousCurrentActorId = ref('')
const lastHandledActionSheetRequestId = ref(0)

const playerUnitId = computed(() => {
  if (!props.externalPlayer) return ''
  if (props.externalPlayer.id?.trim()) return props.externalPlayer.id.trim()
  const name = props.externalPlayer.name?.trim()
  return name ? `player_${name}` : ''
})

const combatActive = computed(() => {
  const participants = props.combat?.participants
  return !!(props.combat && participants && typeof participants === 'object' && Object.keys(participants).length > 0)
})

const currentActorId = computed(() => {
  const raw = props.combat?.current_actor_id
  return typeof raw === 'string' ? raw.trim() : ''
})

const hpUnits = computed<HpOverviewUnit[]>(() => {
  const participants = props.combat?.participants
  const initiativeOrder = Array.isArray(props.combat?.initiative_order)
    ? props.combat?.initiative_order.filter((id: unknown): id is string => typeof id === 'string' && id.trim().length > 0)
    : []
  const initiativeIndex = new Map(initiativeOrder.map((id: string, index: number) => [id, index]))
  const units: HpOverviewUnit[] = []

  if (participants && typeof participants === 'object') {
    Object.values(participants).forEach((participant) => {
      if (!participant || typeof participant !== 'object') return
      const participantRecord = participant as Record<string, any>
      const id = normalizeText(participantRecord.id)
      if (!id) return
      units.push({
        id,
        name: normalizeText(participantRecord.name) || id,
        hp: toNumber(participantRecord.hp),
        maxHp: Math.max(1, toNumber(participantRecord.max_hp)),
        side: resolveSide(normalizeText(participantRecord.side), id, playerUnitId.value),
        sideLabel: resolveSideLabel(normalizeText(participantRecord.side), id, playerUnitId.value),
        initiative: toNumber(participantRecord.initiative),
        initiativeRank: initiativeIndex.has(id) ? initiativeIndex.get(id)! : null,
        initiativeLabel: initiativeIndex.has(id) ? `先攻 #${initiativeIndex.get(id)! + 1}` : '',
        isCurrentActor: id === currentActorId.value,
        isPlayerUnit: id === playerUnitId.value,
      })
    })
  }

  if (props.externalPlayer && playerUnitId.value && !units.some((unit) => unit.id === playerUnitId.value)) {
    units.push({
      id: playerUnitId.value,
      name: props.externalPlayer.name || '玩家',
      hp: props.externalPlayer.hp || 0,
      maxHp: Math.max(1, props.externalPlayer.max_hp || 1),
      side: 'player',
      sideLabel: '玩家',
      initiative: 0,
      initiativeRank: null,
      initiativeLabel: '',
      isCurrentActor: playerUnitId.value === currentActorId.value,
      isPlayerUnit: true,
    })
  }

  return units.sort((left, right) => compareUnitOrder(left, right, currentActorId.value))
})

const combatRound = computed(() => {
  const round = props.combat?.round
  return typeof round === 'number' && Number.isFinite(round) ? round : 1
})

const activeActorLabel = computed(() => {
  const currentActor = hpUnits.value.find((unit) => unit.isCurrentActor)
  if (!currentActor) return '等待回合开始'
  return `${currentActor.name} 正在行动`
})

const currentActor = computed(() => hpUnits.value.find((unit) => unit.isCurrentActor) ?? null)

const canOpenControlledActions = computed(() => {
  const actor = currentActor.value
  return combatActive.value && !!actor && actor.side !== 'enemy'
})

const targetOptions = computed<CombatTargetOption[]>(() => {
  return hpUnits.value
    .filter((unit) => unit.side === 'enemy' && unit.hp > 0)
    .map((unit) => ({
      id: unit.id,
      name: unit.name,
      side: unit.side,
      hp: unit.hp,
      maxHp: unit.maxHp,
    }))
})

const selectedEnemyTarget = computed<CombatTargetOption | null>(() => {
  const selected = props.selectedUnit
  if (!selected || selected.side !== 'enemy' || selected.isDead) return null
  return targetOptions.value.find((unit) => unit.id === selected.id) ?? null
})

watch(
  currentActorId,
  (actorId) => {
    if (!actorId || actorId === previousCurrentActorId.value) return
    previousCurrentActorId.value = actorId
  },
  { immediate: true },
)

watch(
  () => props.actionSheetRequestId ?? 0,
  (requestId) => {
    if (!requestId || requestId === lastHandledActionSheetRequestId.value) return
    lastHandledActionSheetRequestId.value = requestId
    if (!canOpenControlledActions.value) return
    if (selectedEnemyTarget.value && props.selectedUnit) {
      props.requestCombatActionPanel?.({
        ...props.selectedUnit,
        hp: selectedEnemyTarget.value.hp,
        maxHp: selectedEnemyTarget.value.maxHp,
      })
      return
    }
    props.requestCombatActionPanel?.(null)
  },
  { immediate: true },
)

const handleUnitClick = (unit: HpOverviewUnit) => {
  if (!unit.isCurrentActor || unit.side === 'enemy' || !canOpenControlledActions.value) return
  props.requestCombatActionPanel?.(null)
}

function compareUnitOrder(left: HpOverviewUnit, right: HpOverviewUnit, currentId: string): number {
  const leftCurrentPriority = left.id === currentId ? -1 : 0
  const rightCurrentPriority = right.id === currentId ? -1 : 0
  if (leftCurrentPriority !== rightCurrentPriority) {
    return leftCurrentPriority - rightCurrentPriority
  }

  const leftRank = left.initiativeRank ?? Number.MAX_SAFE_INTEGER
  const rightRank = right.initiativeRank ?? Number.MAX_SAFE_INTEGER
  if (leftRank !== rightRank) return leftRank - rightRank

  if (left.initiative !== right.initiative) return right.initiative - left.initiative
  return left.name.localeCompare(right.name, 'zh-Hans-CN')
}

function resolveSide(side: string, id: string, playerId: string): string {
  if (id === playerId) return 'player'
  if (side === 'ally' || side === 'enemy' || side === 'neutral') return side
  return 'ally'
}

function resolveSideLabel(side: string, id: string, playerId: string): string {
  const resolved = resolveSide(side, id, playerId)
  if (resolved === 'player') return '玩家'
  if (resolved === 'ally') return '友方'
  if (resolved === 'enemy') return '敌方'
  return '中立'
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function toNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

</script>

<style scoped>
.hp-overview {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.overview-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 0;
  background:
    linear-gradient(180deg, rgba(35, 29, 24, 0.96) 0%, rgba(20, 18, 17, 0.94) 100%);
  border: none;
  box-shadow:
    inset 0 12px 18px -16px rgba(255, 248, 235, 0.12),
    inset 0 -18px 24px -20px rgba(255, 248, 235, 0.08);
}

.banner-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #efe4cf;
}

.banner-label {
  color: #c6a06e;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.banner-round {
  flex-shrink: 0;
  color: #dcc092;
  font-size: 13px;
}

.hp-unit-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding-right: 6px;
  margin-top: -1px;
  border-radius: 0;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(20, 18, 17, 0.94) 0%, rgba(16, 15, 15, 0.94) 100%);
  border: none;
  box-shadow:
    inset 0 20px 24px -22px rgba(0, 0, 0, 0.2);
}

.hp-overview-item {
  width: 100%;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: none;
  border-radius: 0;
  background: rgba(18, 16, 15, 0.95);
  color: inherit;
  text-align: left;
  transition:
    transform 0.26s ease,
    background 0.26s ease,
    box-shadow 0.26s ease;
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.04);
}

/* 中文注释：左侧高光改为独立光带，避免 blur 阴影把亮度污染到上下边界。 */
.hp-overview-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  right: 0;
  opacity: 0.6;
  background: linear-gradient(90deg, rgba(255, 244, 219, 0.3) 0%, rgba(255, 244, 219, 0.1) 14%, rgba(255, 244, 219, 0.03) 62%, rgba(255, 244, 219, 0) 100%);
  pointer-events: none;
  transform-origin: left center;
  animation: neutralGlow 2.4s ease-in-out infinite;
  transition:
    opacity 0.26s ease,
    transform 0.26s ease,
    background 0.26s ease;
}

.hp-overview-item + .hp-overview-item {
  margin-top: 0;
}

.hp-overview-item:first-child {
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.05);
}

.hp-overview-item:last-child {
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.04);
}

.hp-overview-item:disabled {
  opacity: 1;
}

.hp-overview-item.is-clickable {
  cursor: pointer;
}

.hp-overview-item.is-clickable:hover {
  transform: translateX(2px);
  background: rgba(18, 16, 15, 0.96);
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.08);
}

.hp-overview-item.is-clickable:hover::before {
  opacity: 0.5;
  transform: scaleX(1.04);
}

.hp-overview-item.is-active {
  background: rgba(18, 16, 15, 0.96);
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.08);
}

.hp-overview-item.is-enemy-turn {
  box-shadow:
    inset 1px 0 0 rgba(255, 232, 232, 0.12);
}

.hp-overview-item.is-enemy-turn::before {
  opacity: 0.92;
  background: linear-gradient(90deg, rgba(239, 68, 68, 0.64) 0%, rgba(239, 68, 68, 0.28) 18%, rgba(239, 68, 68, 0.08) 62%, rgba(239, 68, 68, 0) 100%);
  animation: hostileGlow 1.8s ease-in-out infinite;
}

.hp-overview-item.is-player-turn {
  box-shadow:
    inset 1px 0 0 rgba(255, 244, 219, 0.14);
}

.hp-overview-item.is-player-turn::before {
  opacity: 0.92;
  background: linear-gradient(90deg, rgba(245, 199, 103, 0.66) 0%, rgba(245, 199, 103, 0.28) 18%, rgba(245, 199, 103, 0.08) 62%, rgba(245, 199, 103, 0) 100%);
  animation: allyGlow 1.8s ease-in-out infinite;
}

.item-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.meta-main,
.meta-side {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta-side {
  align-items: flex-end;
}

.unit-name {
  color: #efede7;
  font-size: 15px;
  font-weight: 600;
}

.turn-badge,
.initiative-badge,
.side-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
}

.turn-badge {
  background: rgba(214, 177, 97, 0.14);
  color: #e2c68f;
}

.initiative-badge {
  background: rgba(255, 255, 255, 0.08);
  color: #b1b5bf;
}

.side-badge {
  background: rgba(255, 255, 255, 0.06);
}

.side-player,
.side-ally {
  color: #d7c08a;
}

.side-enemy {
  color: #ef8a8a;
}

.side-neutral {
  color: #bfc7d3;
}

.hp-value {
  color: #a8adba;
  font-size: 13px;
  font-family: monospace;
}

.initiative-list-move,
.initiative-list-enter-active,
.initiative-list-leave-active {
  transition: all 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

.initiative-list-enter-from,
.initiative-list-leave-to {
  opacity: 0;
  transform: translateY(14px);
}

.initiative-list-leave-active {
  position: absolute;
  width: calc(100% - 32px);
}

.empty-state {
  color: #8e8e93;
  font-style: italic;
  text-align: center;
  padding: 20px 0;
}

@keyframes neutralGlow {
  0%, 100% {
    opacity: 0.6;
    transform: scaleX(1);
  }
  50% {
    opacity: 0.72;
    transform: scaleX(1.04);
  }
}

@keyframes hostileGlow {
  0%, 100% {
    opacity: 0.82;
    transform: scaleX(1);
  }
  50% {
    opacity: 1;
    transform: scaleX(1.18);
  }
}

@keyframes allyGlow {
  0%, 100% {
    opacity: 0.82;
    transform: scaleX(1);
  }
  50% {
    opacity: 1;
    transform: scaleX(1.18);
  }
}

@media (max-width: 768px) {
  .overview-banner,
  .item-meta {
    flex-direction: column;
  }

  .meta-side {
    align-items: flex-start;
  }
}
</style>
