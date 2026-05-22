<!-- frontend/src/components/Chat/SideCharacterPanel/CharacterSidebar.vue -->
<template>
  <div class="character-sidebar">
    <div ref="workspaceRef" class="workspace-stack" :class="{ locked: !isContextAvailable }">
      <section class="workspace-screen workspace-primary">
        <div class="panel-header">
          <div class="panel-header-main">
            <div class="current-subject-copy">
              <span class="current-subject-label">当前角色</span>
              <h3>{{ activeSubjectName }}</h3>
            </div>

            <div
              v-if="viewTargets.length > 1"
              ref="subjectRailRef"
              class="subject-rail"
              :class="{ open: isSubjectRailOpen }"
            >
              <button
                class="subject-rail-toggle"
                :class="{ open: isSubjectRailOpen }"
                type="button"
                :title="isSubjectRailOpen ? '收起角色选择' : '展开角色选择'"
                @click="toggleSubjectRail"
              >
                <ArrowLeftRight :size="14" stroke-width="1.7" />
                <span class="subject-rail-toggle-label">{{ isSubjectRailOpen ? '收起' : '切换角色' }}</span>
              </button>

              <div v-if="isSubjectRailOpen" class="subject-rail-scroll" aria-label="切换角色视角">
                <button
                  v-for="target in viewTargets"
                  :key="target.id"
                  type="button"
                  class="subject-btn"
                  :class="{ active: target.id === activeSubjectId }"
                  :title="`切换到${target.name}`"
                  @click="selectSubject(target.id)"
                >
                  {{ target.name }}
                </button>
              </div>
            </div>
          </div>

          <div class="panel-header-actions">
            <div ref="switcherRef" class="panel-switcher">
              <button
                class="view-toggle-btn"
                :class="{ active: isMenuOpen }"
                @click="toggleMenu"
                title="切换侧栏面板"
              >
                <ArrowLeftRight :size="16" stroke-width="1.5" />
                <span class="switcher-label">切换</span>
              </button>

              <Transition name="panel-menu">
                <div v-if="isMenuOpen" class="panel-menu">
                  <button
                    v-for="panel in panelOrder"
                    :key="panel"
                    class="panel-menu-item"
                    :class="{ active: activePanel === panel }"
                    @click="selectPanel(panel)"
                  >
                    {{ panelTitles[panel] }}
                  </button>
                </div>
              </Transition>
            </div>

            <button
              v-if="showLeftRailToggleButton"
              class="left-rail-toggle-btn"
              type="button"
              :title="leftRailToggleTitle"
              @click="toggleLeftRailMode"
            >
              {{ leftRailToggleLabel }}
            </button>
          </div>
        </div>

        <div class="panel-scrollable-content">
          <SpaceMap
            v-show="activePanel !== 'inventory'"
            :space="space"
            :player="externalPlayer"
            :combat="combat"
            :scene-units="sceneUnits"
            :dead-units="deadUnits"
            :send-tactical-move-request="sendTacticalMoveRequest"
            @selected-unit-change="handleSelectedUnitChange"
            @request-action-sheet="handleRequestActionSheet"
          />

          <CharacterPanel
            v-if="activePanel === 'character'"
            :external-player="displayedCharacter"
          />
          <InventoryPanel
            v-else
            :external-player="displayedCharacter"
          />
        </div>
      </section>

      <section ref="contextScreenRef" class="workspace-screen workspace-context">
        <div class="context-panel">
          <div v-if="contextPanelMode === 'combat-action'" class="context-header context-header-compact">
            <button class="context-back-btn" type="button" @click="scrollToPrimaryPanel">
              返回上层
            </button>
          </div>

          <div class="context-body">
            <div v-if="contextPanelMode !== 'combat-action'" class="context-empty-state"></div>

            <CombatActionSheet
              v-else
              class="context-action-sheet"
              :open="true"
              :actor-name="controlledActorName"
              :groups="actionGroups"
              :selected-target-name="selectedTargetName"
              :disabled-end-turn="!canEndCurrentTurn"
              :preferred-target="preferredActionTarget"
              :target-options="targetOptions"
              @close="scrollToPrimaryPanel"
              @submit="handleActionSubmit"
              @submit-with-target="handleTargetedActionSubmit"
              @blocked="handleActionBlocked"
              @end-turn="handleEndTurn"
            />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeftRight } from 'lucide-vue-next'
import SpaceMap from '../SpaceMap.vue'
import CharacterPanel from './CharacterPanel.vue'
import InventoryPanel from './InventoryPanel.vue'
import CombatActionSheet from '../Combat/CombatActionSheet.vue'
import { LEFT_RAIL_MODE_EVENT, overrideLeftRailMode } from '../../../Services_/leftRailService'
import type { PlayerState } from '../../../Services_/characterStateService'
import type { AvailabilitySelectionUnit } from '../../../Services_/actionAvailabilityService'
import {
  buildCombatActionCommand,
  buildCombatActionMenu,
  type CombatActionMenuGroup,
  type CombatActionMenuItem,
} from '../../../Services_/combatActionCatalog'

type SidebarPanelMode = 'character' | 'inventory'
type ContextPanelMode = 'empty' | 'combat-action'
type CombatTargetOption = {
  id: string
  name: string
  side: string
  hp: number
  maxHp: number
}

const props = defineProps<{
  externalPlayer: PlayerState | null
  combat?: any | null
  space?: any | null
  sceneUnits?: Record<string, any> | null
  deadUnits?: Record<string, any> | null
  activeAllyId?: string
  sendTacticalMoveRequest?: ((message: string) => Promise<void>) | null
  sendCombatActionRequest?: ((message: string) => Promise<void>) | null
  endCombatTurnRequest?: ((actorId: string) => Promise<void>) | null
}>()

const emit = defineEmits<{
  selectedUnitChange: [unit: AvailabilitySelectionUnit | null]
  requestActionSheet: [unit: AvailabilitySelectionUnit]
  actionNotice: [text: string]
}>()

const panelOrder: SidebarPanelMode[] = ['character', 'inventory']
const panelTitles: Record<SidebarPanelMode, string> = {
  character: '角色状态',
  inventory: '背包',
}

// 侧栏统一维护模式切换，避免 ChatPage 直接了解内部结构
const activePanel = ref<SidebarPanelMode>('character')
const isMenuOpen = ref(false)
const switcherRef = ref<HTMLElement | null>(null)
const selectedUnit = ref<AvailabilitySelectionUnit | null>(null)
const leftRailMode = ref<'navigation' | 'combat'>('navigation')
const selectedSubjectId = ref('player')
const contextPanelMode = ref<ContextPanelMode>('empty')
const preferredActionTarget = ref<CombatTargetOption | null>(null)
const workspaceRef = ref<HTMLElement | null>(null)
const contextScreenRef = ref<HTMLElement | null>(null)

const activePanelTitle = computed(() => panelTitles[activePanel.value])
const showLeftRailToggleButton = computed(() => isCombatActive(props.combat))
const leftRailToggleLabel = computed(() => leftRailMode.value === 'combat' ? '返回导航' : '显示时间轴')
const leftRailToggleTitle = computed(() => leftRailMode.value === 'combat' ? '切回默认导航栏' : '切回战斗时间轴')
const viewTargets = computed(() => buildViewTargets(props.externalPlayer, props.combat, props.sceneUnits))
const activeSubjectName = computed(() => {
  const target = viewTargets.value.find((item) => item.id === activeSubjectId.value)
  return target?.name || props.externalPlayer?.name?.trim() || activePanelTitle.value
})
const activeSubjectId = computed(() => {
  if (viewTargets.value.some((target) => target.id === selectedSubjectId.value)) {
    return selectedSubjectId.value
  }
  return 'player'
})
const displayedCharacter = computed(() => {
  const target = viewTargets.value.find((item) => item.id === activeSubjectId.value)
  return target?.character ?? props.externalPlayer
})
const isSubjectRailOpen = ref(false)
const subjectRailRef = ref<HTMLElement | null>(null)
const playerUnitId = computed(() => {
  const player = props.externalPlayer
  if (!player) return ''
  if (player.id?.trim()) return player.id.trim()
  const name = player.name?.trim()
  return name ? `player_${name}` : ''
})
const currentActorId = computed(() => normalizeText(props.combat?.current_actor_id))
const currentControlledActor = computed(() => {
  const actorId = currentActorId.value
  if (!actorId) return null
  if (actorId === playerUnitId.value && props.externalPlayer) {
    return {
      id: actorId,
      side: 'player',
      name: props.externalPlayer.name?.trim() || '玩家',
      state: props.externalPlayer,
    }
  }

  const actor = props.combat?.participants?.[actorId]
  if (!actor || typeof actor !== 'object') return null
  const actorRecord = actor as Record<string, any>
  const side = normalizeText(actorRecord.side) || 'ally'
  if (side === 'enemy') return null
  return {
    id: actorId,
    side,
    name: normalizeText(actorRecord.name) || actorId,
    state: normalizeAllyCharacter(actorRecord, actorId),
  }
})
const canOpenContextActionSheet = computed(() => {
  return !!currentControlledActor.value && currentControlledActor.value.side !== 'enemy'
})
const controlledActorName = computed(() => currentControlledActor.value?.name || props.externalPlayer?.name?.trim() || '当前单位')
const selectedTargetName = computed(() => preferredActionTarget.value?.name || selectedUnit.value?.name || '')
const actionGroups = computed<CombatActionMenuGroup[]>(() => {
  const actorState = currentControlledActor.value?.state
  if (!actorState) return []
  return buildCombatActionMenu({
    player: actorState,
    combat: props.combat ?? null,
    space: props.space ?? null,
    selectedUnit: (preferredActionTarget.value
      ? {
        id: preferredActionTarget.value.id,
        name: preferredActionTarget.value.name,
        side: preferredActionTarget.value.side,
        x: selectedUnit.value?.x ?? 0,
        y: selectedUnit.value?.y ?? 0,
        hp: preferredActionTarget.value.hp,
        maxHp: preferredActionTarget.value.maxHp,
        ac: selectedUnit.value?.ac,
        conditions: selectedUnit.value?.conditions,
        isDead: false,
      }
      : selectedUnit.value) ?? null,
  })
})
const targetOptions = computed<CombatTargetOption[]>(() => {
  const participants = props.combat?.participants
  const options: CombatTargetOption[] = []

  if (props.externalPlayer && toNumber(props.externalPlayer.hp) > 0) {
    options.push({
      id: playerUnitId.value || 'player',
      name: props.externalPlayer.name?.trim() || '玩家',
      side: 'player',
      hp: toNumber(props.externalPlayer.hp),
      maxHp: Math.max(1, toNumber(props.externalPlayer.max_hp)),
    })
  }

  if (!participants || typeof participants !== 'object') return options

  Object.values(participants)
    .filter((participant) => participant && typeof participant === 'object')
    .map((participant) => participant as Record<string, any>)
    .filter((participant) => toNumber(participant.hp) > 0)
    .forEach((participant) => {
      const side = normalizeCombatTargetSide(participant.side)
      if (!side) return
      const id = normalizeText(participant.id) || normalizeText(participant.name)
      if (!id || (side === 'player' && id === playerUnitId.value)) return

      options.push({
        id,
        name: normalizeText(participant.name) || id || '目标单位',
        side,
        hp: toNumber(participant.hp),
        maxHp: Math.max(1, toNumber(participant.max_hp)),
      })
    })

  return options
})
const canEndCurrentTurn = computed(() => !!currentControlledActor.value)
const isContextAvailable = computed(() => canOpenContextActionSheet.value)

// 切换菜单改成显式选择，避免轮播式切换误触
const toggleMenu = () => {
  isMenuOpen.value = !isMenuOpen.value
}

const setViewMode = (mode: SidebarPanelMode) => {
  activePanel.value = mode
  isMenuOpen.value = false
}

const selectPanel = (mode: SidebarPanelMode) => {
  setViewMode(mode)
}

const selectSubject = (subjectId: string) => {
  selectedSubjectId.value = subjectId
  isSubjectRailOpen.value = false
}

const toggleSubjectRail = () => {
  isSubjectRailOpen.value = !isSubjectRailOpen.value
}

// 侧栏继续做轻量转发层，避免聊天页直接依赖地图组件。
const handleSelectedUnitChange = (unit: AvailabilitySelectionUnit | null) => {
  selectedUnit.value = unit
  emit('selectedUnitChange', unit)
}

// 中文注释：地图只上抛“请打开战斗动作面板”的意图，避免侧栏知道双击规则细节。
const handleRequestActionSheet = (unit: AvailabilitySelectionUnit) => {
  openCombatActionPanel(unit)
  emit('requestActionSheet', unit)
}

const scrollToContextPanel = () => {
  const workspace = workspaceRef.value
  const contextScreen = contextScreenRef.value
  if (!workspace || !contextScreen) return
  workspace.scrollTo({ top: contextScreen.offsetTop, behavior: 'smooth' })
}

const scrollToPrimaryPanel = () => {
  workspaceRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
}

const openCombatActionPanel = (preferredTarget?: AvailabilitySelectionUnit | CombatTargetOption | null) => {
  if (!canOpenContextActionSheet.value) return
  const target = preferredTarget ?? selectedUnit.value ?? null
  preferredActionTarget.value = target
    ? {
        id: target.id,
        name: target.name,
        side: target.side,
        hp: typeof target.hp === 'number' ? target.hp : 0,
        maxHp: typeof target.maxHp === 'number' ? target.maxHp : 1,
      }
    : null
  contextPanelMode.value = 'combat-action'
  scrollToContextPanel()
}

const handleActionSubmit = async (item: CombatActionMenuItem) => {
  if (!props.sendCombatActionRequest) return
  await props.sendCombatActionRequest(item.command)
  scrollToPrimaryPanel()
}

const handleTargetedActionSubmit = async (payload: { item: CombatActionMenuItem; target: CombatTargetOption }) => {
  if (!props.sendCombatActionRequest) return
  await props.sendCombatActionRequest(
    buildCombatActionCommand(payload.item.commandPrefix, payload.item.targetMode, payload.target),
  )
  scrollToPrimaryPanel()
}

const handleActionBlocked = (reason: string) => {
  emit('actionNotice', reason)
}

const handleEndTurn = async () => {
  if (!props.endCombatTurnRequest || !currentControlledActor.value) return
  await props.endCombatTurnRequest(currentControlledActor.value.id)
  scrollToPrimaryPanel()
}

const toggleLeftRailMode = () => {
  overrideLeftRailMode(leftRailMode.value === 'combat' ? 'navigation' : 'combat')
}

const handleLeftRailMode = (event: Event) => {
  const mode = (event as CustomEvent<'navigation' | 'combat'>).detail
  leftRailMode.value = mode
}

watch(
  () => props.activeAllyId,
  (allyId) => {
    if (!allyId) return
    if (viewTargets.value.some((target) => target.id === allyId)) {
      selectedSubjectId.value = allyId
      activePanel.value = 'character'
    }
  },
  { immediate: true },
)

watch(
  viewTargets,
  (targets) => {
    if (!targets.some((target) => target.id === selectedSubjectId.value)) {
      selectedSubjectId.value = 'player'
    }
  },
  { immediate: true },
)

watch(
  isContextAvailable,
  (available, previousAvailable) => {
    if (!available) {
      preferredActionTarget.value = null
      contextPanelMode.value = 'empty'
      workspaceRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }

    contextPanelMode.value = 'combat-action'
    if (!previousAvailable) {
      preferredActionTarget.value = null
      scrollToContextPanel()
    }
  },
  { immediate: true },
)

// 点击外部区域时关闭浮层，保持轻量原生感
const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target as Node
  if (switcherRef.value?.contains(target)) return
  if (subjectRailRef.value?.contains(target)) return
  isMenuOpen.value = false
  isSubjectRailOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  window.addEventListener(LEFT_RAIL_MODE_EVENT, handleLeftRailMode as EventListener)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
  window.removeEventListener(LEFT_RAIL_MODE_EVENT, handleLeftRailMode as EventListener)
})

defineExpose({
  setViewMode,
  selectPanel,
  selectSubject,
  openCombatActionPanel,
})

function isCombatActive(combat: unknown): boolean {
  if (!combat || typeof combat !== 'object') return false
  const participants = (combat as Record<string, any>).participants
  return !!(participants && typeof participants === 'object' && Object.keys(participants).length > 0)
}

type ViewTarget = {
  id: string
  name: string
  character: PlayerState
}

function buildViewTargets(
  player: PlayerState | null,
  combat: any | null | undefined,
  sceneUnits: Record<string, any> | null | undefined,
): ViewTarget[] {
  const targets: ViewTarget[] = []
  if (player) {
    targets.push({ id: 'player', name: player.name?.trim() || '玩家', character: player })
  }

  const allies = new Map<string, Record<string, any>>()
  collectAllies(allies, combat?.participants)
  collectAllies(allies, sceneUnits)
  allies.forEach((unit, unitId) => {
    targets.push({
      id: unitId,
      name: normalizeText(unit.name) || unitId,
      character: normalizeAllyCharacter(unit, unitId),
    })
  })
  return targets
}

function collectAllies(target: Map<string, Record<string, any>>, source: unknown): void {
  if (!source || typeof source !== 'object') return
  Object.entries(source as Record<string, any>).forEach(([key, value]) => {
    if (!value || typeof value !== 'object') return
    const unit = value as Record<string, any>
    if (normalizeText(unit.side) !== 'ally') return
    const id = normalizeText(unit.id) || key
    target.set(id, { ...unit, id })
  })
}

function normalizeAllyCharacter(unit: Record<string, any>, fallbackId: string): PlayerState {
  // 中文注释：队友沿用角色面板，不把友方状态强行塞进玩家对象。
  return {
    id: normalizeText(unit.id) || fallbackId,
    name: normalizeText(unit.name) || fallbackId,
    role_class: normalizeText(unit.role_class) || '队友',
    level: toNumber(unit.level) || 1,
    hp: toNumber(unit.hp),
    max_hp: Math.max(1, toNumber(unit.max_hp)),
    temp_hp: toNumber(unit.temp_hp),
    ac: toNumber(unit.ac) || toNumber(unit.base_ac) || 10,
    base_ac: toNumber(unit.base_ac) || undefined,
    abilities: isRecord(unit.abilities) ? unit.abilities as Record<string, number> : {},
    modifiers: isRecord(unit.modifiers) ? unit.modifiers as Record<string, number> : {},
    conditions: Array.isArray(unit.conditions) ? unit.conditions : [],
    resources: isRecord(unit.resources) ? unit.resources as Record<string, number> : {},
    weapons: Array.isArray(unit.weapons) ? unit.weapons : [],
    coins: isRecord(unit.coins) ? unit.coins as Record<string, number> : undefined,
    inventory: Array.isArray(unit.inventory) ? unit.inventory : [],
    known_spells: Array.isArray(unit.known_spells) ? unit.known_spells : [],
    known_cantrips: Array.isArray(unit.known_cantrips) ? unit.known_cantrips : [],
    spellcasting_ability: normalizeText(unit.spellcasting_ability),
    concentrating_on: typeof unit.concentrating_on === 'string' ? unit.concentrating_on : null,
    xp: typeof unit.xp === 'number' ? unit.xp : undefined,
    class_features: Array.isArray(unit.class_features) || isRecord(unit.class_features) ? unit.class_features : [],
    arcane_tradition: normalizeText(unit.arcane_tradition) || undefined,
    speed: toNumber(unit.speed) || undefined,
    movement_left: toNumber(unit.movement_left),
    action_available: unit.action_available !== false,
    extra_action_available: unit.extra_action_available === true,
    bonus_action_available: unit.bonus_action_available !== false,
    reaction_available: unit.reaction_available !== false,
  }
}

function isRecord(value: unknown): value is Record<string, any> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function toNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function normalizeCombatTargetSide(value: unknown): 'player' | 'ally' | 'enemy' | null {
  const side = normalizeText(value)
  if (side === 'player' || side === 'ally' || side === 'enemy') return side
  return null
}

</script>

<style scoped>
.character-sidebar {
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

.workspace-stack {
  height: 100%;
  overflow-y: auto;
  scroll-behavior: smooth;
  scroll-snap-type: y proximity;
  -ms-overflow-style: none;
  scrollbar-width: none;
}

.workspace-stack::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.workspace-screen {
  min-height: 100%;
  scroll-snap-align: start;
}

.workspace-primary {
  display: flex;
  flex-direction: column;
}

.workspace-context {
  padding: 14px 16px 16px;
  background:
    linear-gradient(180deg, rgba(18, 19, 24, 0.96) 0%, rgba(12, 13, 17, 0.98) 100%);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.workspace-stack.locked .workspace-context {
  min-height: 0;
  height: 0;
  padding-top: 0;
  padding-bottom: 0;
  border-top: none;
  overflow: hidden;
}

.panel-header {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: stretch;
  gap: 8px;
  padding: 16px 16px 10px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  position: relative;
}

.panel-header-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}

.current-subject-copy {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  width: 100%;
}

.current-subject-label {
  flex-shrink: 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: rgba(243, 234, 215, 0.88);
}

.panel-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
  line-height: 1.1;
  color: rgba(255, 255, 255, 0.68);
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.subject-rail {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  max-width: 100%;
  min-height: 30px;
  padding: 0;
  overflow: hidden;
  transition: width 0.22s ease, background 0.22s ease, border-color 0.22s ease, padding 0.22s ease;
}

.subject-rail.open {
  width: 100%;
  padding: 4px 8px 4px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.subject-rail-toggle {
  width: fit-content;
  max-width: 100%;
  min-height: 22px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 0 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(255, 255, 255, 0.58);
  cursor: pointer;
  transition: all 0.18s ease;
}

.subject-rail-toggle:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #f2e7d2;
}

.subject-rail-toggle.open {
  width: 32px;
  min-width: 32px;
  height: 32px;
  padding: 0;
  background: rgba(201, 168, 123, 0.12);
  color: #e8d2a7;
  border-color: rgba(201, 168, 123, 0.18);
}

.subject-rail-toggle-label {
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}

.subject-rail-toggle.open .subject-rail-toggle-label {
  display: none;
}

.subject-rail-scroll {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(201, 168, 123, 0.35) transparent;
  padding: 0 2px 2px 0;
}

.subject-rail-scroll::-webkit-scrollbar {
  height: 4px;
}

.subject-rail-scroll::-webkit-scrollbar-thumb {
  background: rgba(201, 168, 123, 0.3);
  border-radius: 999px;
}

.panel-header-actions {
  flex-shrink: 0;
  width: 78px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 8px;
}

.subject-btn {
  min-height: 24px;
  flex-shrink: 0;
  max-width: 108px;
  padding: 0 9px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  color: rgba(255, 255, 255, 0.66);
  font-size: 11px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: all 0.18s ease;
}

.subject-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #f2e7d2;
}

.subject-btn.active {
  border-color: rgba(201, 168, 123, 0.36);
  background: rgba(201, 168, 123, 0.14);
  color: #efd9ad;
}

.left-rail-toggle-btn {
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid rgba(201, 168, 123, 0.22);
  border-radius: 12px;
  background: rgba(201, 168, 123, 0.08);
  color: #dcc092;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.18s ease;
  white-space: nowrap;
}

.left-rail-toggle-btn:hover {
  background: rgba(201, 168, 123, 0.14);
  border-color: rgba(201, 168, 123, 0.34);
}

.panel-switcher {
  position: relative;
}

.view-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding: 0 8px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-size: 13px;
}

.view-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(4px);
  color: rgba(255, 255, 255, 0.75);
}

.view-toggle-btn.active {
  background: rgba(66, 184, 131, 0.12);
  color: rgba(233, 213, 184, 0.88);
  box-shadow: 0 2px 8px rgba(66, 184, 131, 0.08);
  border-color: rgba(210, 180, 140, 0.24);
}

.switcher-label {
  font-size: 11px;
  font-weight: 470;
  white-space: nowrap;
  letter-spacing: 0.3px;
}

.panel-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 136px;
  padding: 8px;
  border-radius: 16px;
  background: rgba(24, 24, 30, 0.9);
  border: 1px solid rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(18px);
  box-shadow:
    0 10px 32px rgba(0, 0, 0, 0.34),
    0 0 0 1px rgba(255, 255, 255, 0.03) inset;
  z-index: 20;
}

.panel-menu-item {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-height: 34px;
  padding: 8px 12px;
  border: none;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(255, 255, 255, 0.72);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.panel-menu-item:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #f2e7d2;
}

.panel-menu-item.active {
  background: rgba(66, 184, 131, 0.12);
  color: #e6d5b8;
  box-shadow: 0 0 0 1px rgba(210, 180, 140, 0.2) inset;
}

.panel-menu-enter-active,
.panel-menu-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-menu-enter-from,
.panel-menu-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}

.panel-scrollable-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px 16px 16px;
  -ms-overflow-style: none;
  scrollbar-width: none;
}

.panel-scrollable-content::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.context-panel {
  min-height: calc(100vh - 44px);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.context-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.context-heading {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.context-eyebrow {
  color: #c3a473;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.context-title {
  margin: 0;
  color: #f3ead7;
  font-size: 22px;
  line-height: 1.15;
}

.context-back-btn {
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  color: #ddd6c7;
  cursor: pointer;
}

.context-body {
  flex: 1;
  min-height: 0;
}

.context-empty-state {
  min-height: 0;
}

.context-unit-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.unit-card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
}

.unit-card-name {
  color: #f2ede4;
  font-size: 18px;
  font-weight: 600;
}

.unit-card-side {
  font-size: 12px;
}

.unit-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.unit-card-grid div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.unit-card-grid span {
  color: #9ea4af;
  font-size: 12px;
}

.unit-card-grid strong {
  color: #f2ede4;
  font-size: 14px;
}

.unit-card-conditions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.unit-card-condition {
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(239, 68, 68, 0.16);
  color: #f3a5a5;
  font-size: 12px;
}

.context-primary-btn {
  min-height: 42px;
  width: 100%;
  border: 1px solid rgba(210, 180, 140, 0.28);
  border-radius: 14px;
  background: rgba(210, 180, 140, 0.12);
  color: #f0debf;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.context-action-sheet {
  height: 100%;
}

@media (max-width: 720px) {
  .panel-header {
    gap: 6px;
  }

  .current-subject-copy {
    gap: 8px;
  }

  .current-subject-label {
    font-size: 13px;
  }

  .panel-header h3 {
    font-size: 14px;
  }

  .subject-btn {
    max-width: 92px;
  }

  .unit-card-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
