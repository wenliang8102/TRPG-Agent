<template>
  <section v-if="activeMap" class="space-map">
    <div class="map-header" :class="{ collapsed: isMapCollapsed && !!activeMap }">
      <div v-if="!isMapCollapsed" class="map-heading">
        <div class="section-title">战术地图</div>
        <div class="map-subtitle">
          <span class="map-name">{{ activeMap.name }}</span>
          <span class="map-size">{{ formatNumber(activeMap.width) }}x{{ formatNumber(activeMap.height) }} 尺</span>
        </div>
      </div>
      <div v-else class="map-collapsed-label">战术地图已隐藏</div>
      <div class="map-actions">
        <button
          class="move-toggle-btn"
          type="button"
          :disabled="!canEnterMoveMode || isSubmittingMove"
          :title="moveActionTitle"
          @click.stop="toggleMoveMode"
        >
          <Footprints :size="14" stroke-width="1.8" />
        </button>
        <button
          class="map-toggle-btn"
          type="button"
          :title="isMapCollapsed ? '显示地图' : '隐藏地图'"
          @click="toggleMapVisibility"
        >
          <component :is="isMapCollapsed ? Eye : EyeOff" :size="14" stroke-width="1.8" />
        </button>
        <button
          class="focus-player-btn"
          type="button"
          :disabled="!playerVisibleUnit"
          title="定位到主角位置"
          @click="focusPlayer"
        >
          <LocateFixed :size="14" stroke-width="1.8" />
        </button>
      </div>
    </div>

    <div
      v-if="!isMapCollapsed"
      class="map-shell"
      :class="{
        interactive: isMapInteractive,
        panning: isPanning,
        'move-mode': isMoveModeActive,
      }"
      @click="activateMap($event, 'inline')"
      @wheel="handleMapWheel($event, 'inline')"
      @mousedown="handlePanStart($event, 'inline')"
    >
      <svg
        ref="inlineMapCanvasRef"
        class="map-canvas"
        :viewBox="`0 0 ${viewBoxWidth} ${viewBoxHeight}`"
        role="img"
        :aria-label="`${activeMap.name} 平面地图`"
      >
        <g :transform="panTransform">
          <g ref="inlineViewportGroupRef" :transform="viewportTransform">
            <rect
              class="map-bg"
              :x="0"
              :y="0"
              :width="viewBoxWidth"
              :height="viewBoxHeight"
              rx="0"
            />
            <path
              v-for="line in gridLines"
              :key="line.key"
              class="grid-line"
              :d="line.path"
            />
            <g v-if="movePreview" class="move-preview">
              <line
                class="move-preview-line"
                :x1="movePreview.fromScreenX"
                :y1="movePreview.fromScreenY"
                :x2="movePreview.toScreenX"
                :y2="movePreview.toScreenY"
              />
              <circle
                class="move-preview-target"
                :class="{ invalid: isMoveDistanceExceeded }"
                :cx="movePreview.toScreenX"
                :cy="movePreview.toScreenY"
                r="1.5"
                @mousedown.stop.prevent="handleMoveTargetHandleDragStart($event, 'inline')"
              />
            </g>
            <g
              v-for="unit in visibleUnits"
              :key="unit.id"
              class="unit-node"
              :class="[
                { selected: unit.id === selectedUnitId, 'is-dead': unit.isDead, movable: unit.id === playerUnitId && canEnterMoveMode },
                unit.sideClass,
              ]"
              role="button"
              tabindex="0"
              :aria-label="`${unit.name} 坐标 ${formatNumber(unit.x)}, ${formatNumber(unit.y)}`"
              @mousedown="handleUnitDragStart($event, unit.id, 'inline')"
              @click.stop="handleUnitClick(unit.id)"
              @dblclick.stop="handleUnitDoubleClick(unit.id)"
              @keydown.enter.prevent="selectedUnitId = unit.id"
              @keydown.space.prevent="selectedUnitId = unit.id"
            >
              <circle class="unit-aura" :cx="unit.screenX" :cy="unit.screenY" :r="unit.selected ? 2.25 : 1.75" />
              <circle class="unit-dot" :cx="unit.screenX" :cy="unit.screenY" :r="1" />
              <text
                class="unit-label"
                :class="{ expanded: showFullLabels }"
                :x="unit.screenX + (showFullLabels ? 2.2 : 0)"
                :y="unit.screenY - (showFullLabels ? 2 : 3.25)"
                :text-anchor="showFullLabels ? 'start' : 'middle'"
              >
                {{ showFullLabels ? unit.name : unit.initial }}
              </text>
            </g>
          </g>
        </g>
      </svg>
      <div class="axis-row">
        <span class="zoom-indicator">
          {{
            isPanning
              ? '拖动中'
              : isMoveModeActive
                ? moveHintText
                : isMapInteractive
                  ? `缩放 ${zoomScale.toFixed(1)}x；按住 Ctrl + 鼠标拖动`
                  : '点击地图后可滚轮缩放；按住 Ctrl + 鼠标拖动'
          }}
        </span>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="isDetachedMapOpen && activeMap"
        class="detached-map-overlay"
        role="dialog"
        aria-modal="true"
        :aria-label="`${activeMap.name} 独立地图`"
      >
        <div class="detached-map-panel" :style="{ '--detached-map-side': `${detachedMapSide}px` }">
            <div class="detached-map-header">
              <div class="detached-map-heading">
                <div class="section-title detached-map-title">战术地图</div>
                <div class="map-subtitle">
                  <span class="map-name">{{ activeMap.name }}</span>
                  <span class="map-size">{{ formatNumber(activeMap.width) }}x{{ formatNumber(activeMap.height) }} 尺</span>
                </div>
            </div>
            <div class="map-actions detached-map-actions">
              <button
                class="move-toggle-btn"
                type="button"
                :disabled="!canEnterMoveMode || isSubmittingMove"
                :title="moveActionTitle"
                @click.stop="toggleMoveMode"
              >
                <Footprints :size="14" stroke-width="1.8" />
              </button>
              <button
                class="focus-player-btn"
                type="button"
                :disabled="!playerVisibleUnit"
                title="定位到主角位置"
                @click="focusPlayer"
              >
                <LocateFixed :size="14" stroke-width="1.8" />
              </button>
              <button
                class="detached-map-close-btn"
                type="button"
                title="关闭独立地图"
                @click="closeDetachedMap"
              >
                退出
              </button>
            </div>
          </div>

          <div
            class="map-shell detached-map-shell"
            :class="{
              interactive: isMapInteractive,
              panning: isPanning,
              'move-mode': isMoveModeActive,
            }"
            @click="activateMap($event, 'detached')"
            @wheel="handleMapWheel($event, 'detached')"
            @mousedown="handlePanStart($event, 'detached')"
          >
            <svg
              ref="detachedMapCanvasRef"
              class="map-canvas detached-map-canvas"
              :viewBox="`0 0 ${viewBoxWidth} ${viewBoxHeight}`"
              role="img"
              :aria-label="`${activeMap.name} 平面地图`"
            >
              <g :transform="panTransform">
                <g ref="detachedViewportGroupRef" :transform="viewportTransform">
                  <rect
                    class="map-bg"
                    :x="0"
                    :y="0"
                    :width="viewBoxWidth"
                    :height="viewBoxHeight"
                    rx="0"
                  />
                  <path
                    v-for="line in gridLines"
                    :key="`detached-${line.key}`"
                    class="grid-line"
                    :d="line.path"
                  />
                  <g v-if="movePreview" class="move-preview">
                    <line
                      class="move-preview-line"
                      :x1="movePreview.fromScreenX"
                      :y1="movePreview.fromScreenY"
                      :x2="movePreview.toScreenX"
                      :y2="movePreview.toScreenY"
                    />
                    <circle
                      class="move-preview-target"
                      :class="{ invalid: isMoveDistanceExceeded }"
                      :cx="movePreview.toScreenX"
                      :cy="movePreview.toScreenY"
                      r="1.5"
                      @mousedown.stop.prevent="handleMoveTargetHandleDragStart($event, 'detached')"
                    />
                  </g>
                  <g
                    v-for="unit in visibleUnits"
                    :key="`detached-${unit.id}`"
                    class="unit-node"
                    :class="[
                      { selected: unit.id === selectedUnitId, 'is-dead': unit.isDead, movable: unit.id === playerUnitId && canEnterMoveMode },
                      unit.sideClass,
                    ]"
                    role="button"
                    tabindex="0"
                    :aria-label="`${unit.name} 坐标 ${formatNumber(unit.x)}, ${formatNumber(unit.y)}`"
                    @mousedown="handleUnitDragStart($event, unit.id, 'detached')"
                    @click.stop="handleUnitClick(unit.id)"
                    @dblclick.stop="handleUnitDoubleClick(unit.id)"
                    @keydown.enter.prevent="selectedUnitId = unit.id"
                    @keydown.space.prevent="selectedUnitId = unit.id"
                  >
                    <circle class="unit-aura" :cx="unit.screenX" :cy="unit.screenY" :r="unit.selected ? 2.25 : 1.75" />
                    <circle class="unit-dot" :cx="unit.screenX" :cy="unit.screenY" :r="1" />
                    <text
                      class="unit-label"
                      :class="{ expanded: showFullLabels }"
                      :x="unit.screenX + (showFullLabels ? 2.2 : 0)"
                      :y="unit.screenY - (showFullLabels ? 2 : 3.25)"
                      :text-anchor="showFullLabels ? 'start' : 'middle'"
                    >
                      {{ showFullLabels ? unit.name : unit.initial }}
                    </text>
                  </g>
                </g>
              </g>
            </svg>
            <div class="axis-row detached-axis-row">
              <span class="zoom-indicator">
                {{
                  isPanning
                    ? '拖动中'
                    : isMoveModeActive
                      ? moveHintText
                      : isMapInteractive
                        ? `缩放 ${zoomScale.toFixed(1)}x；按住 Ctrl + 鼠标拖动`
                        : '点击地图后可滚轮缩放；按住 Ctrl + 鼠标拖动'
                }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="isDetachedMapOpen && (isMoveModeActive || selectedUnit)"
        class="detached-stats-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="战术地图属性面板"
      >
        <div class="detached-stats-panel">
        <div class="detached-stats-header">
            <div class="section-title">状态面板</div>
          </div>

          <div v-if="isMoveModeActive" class="move-panel detached-map-info">
            <template v-if="moveEligibility">
              <div class="move-panel-head">
                <span class="move-panel-title">{{ movePanelTitle }}</span>
                <span class="move-panel-status" :class="{ active: isMoveModeActive }">
                  {{ isMoveModeActive ? '已开启' : '未开启' }}
                </span>
              </div>
              <div class="move-panel-grid detached-stats-grid">
                <div>
                  <span>{{ moveActorLabel }}</span>
                  <strong>{{ moveEligibility.actorName }}</strong>
                </div>
                <div>
                  <span>{{ moveResourceLabel }}</span>
                  <strong>{{ moveResourceText }}</strong>
                </div>
                <div>
                  <span>起点</span>
                  <strong>({{ formatNumber(moveEligibility.fromX) }}, {{ formatNumber(moveEligibility.fromY) }})</strong>
                </div>
                <div>
                  <span>终点</span>
                  <strong>{{ moveTarget ? `(${formatNumber(moveTarget.x)}, ${formatNumber(moveTarget.y)})` : '待选择' }}</strong>
                </div>
                <div>
                  <span>预计距离</span>
                  <strong>{{ moveDistance === null ? '待选择' : `${formatNumber(moveDistance)} 尺` }}</strong>
                </div>
                <div>
                  <strong :class="{ danger: !isMoveAvailable, 'movable-glow': isMoveAvailable }">
                    {{ moveValidationText }}
                  </strong>
                </div>
              </div>
              <div class="move-panel-actions detached-stats-actions">
                <button
                  class="move-action-btn"
                  type="button"
                  :disabled="isSubmittingMove"
                  @click="toggleMoveMode"
                >
                  {{ isMoveModeActive ? '取消移动' : `开始${movePanelTitle}` }}
                </button>
                <button
                  class="move-action-btn subtle"
                  type="button"
                  :disabled="!isMoveModeActive || !moveTarget || isSubmittingMove"
                  @click="clearMovePreview"
                >
                  清除目标
                </button>
                <button
                  class="move-action-btn primary"
                  type="button"
                  :disabled="!canConfirmMove"
                  @click="submitMoveRequest"
                >
                  {{ isSubmittingMove ? '移动中...' : '确认移动' }}
                </button>
              </div>
            </template>
            <div v-else class="move-disabled-reason">
              {{ moveDisabledReason }}
            </div>
          </div>

          <div v-if="selectedUnit" class="unit-detail detached-map-info">
            <div class="unit-detail-head">
              <span class="unit-name">{{ selectedUnit.name }}</span>
              <span class="unit-id">{{ selectedUnit.id }}</span>
            </div>
            <div class="detail-grid detached-stats-grid">
              <div>
                <span>坐标</span>
                <strong>({{ formatNumber(selectedUnit.x) }}, {{ formatNumber(selectedUnit.y) }})</strong>
              </div>
              <div>
                <span>阵营</span>
                <strong>{{ sideLabel(selectedUnit.side) }}</strong>
              </div>
              <div v-if="selectedUnit.hp !== undefined">
                <span>生命值</span>
                <strong>{{ selectedUnit.hp }} / {{ selectedUnit.max_hp ?? '?' }}</strong>
              </div>
              <div v-if="selectedUnit.ac !== undefined">
                <span>护甲</span>
                <strong>{{ selectedUnit.ac }}</strong>
              </div>
            </div>
            <div v-if="selectedUnit.conditions.length" class="condition-line">
              <span v-for="condition in selectedUnit.conditions" :key="condition" class="mini-condition">
                {{ condition }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Eye, EyeOff, Footprints, LocateFixed } from 'lucide-vue-next'

type PlaneMap = {
  id: string
  name: string
  width: number
  height: number
  grid_size?: number
}

type UnitInfo = {
  id: string
  name?: string
  side?: string
  hp?: number
  max_hp?: number
  ac?: number
  conditions?: Array<{ id?: string; name_cn?: string }>
}

type VisibleUnit = {
  id: string
  name: string
  side: string
  sideClass: string
  initial: string
  x: number
  y: number
  screenX: number
  screenY: number
  selected: boolean
  isDead: boolean
  hp?: number
  max_hp?: number
  ac?: number
  conditions: string[]
}

type MoveEligibility = {
  mode: 'combat' | 'explore'
  actorId: string
  actorName: string
  fromX: number
  fromY: number
  movementLeft?: number
}

type SelectedUnitPayload = {
  id: string
  name: string
  side: string
  x: number
  y: number
  hp?: number
  maxHp?: number
  ac?: number
  conditions?: string[]
  isDead?: boolean
}

type LooseRecord = Record<string, any>

const props = defineProps<{
  space: any | null
  player: any | null
  combat?: any | null
  sceneUnits?: Record<string, any> | null
  deadUnits?: Record<string, any> | null
  sendTacticalMoveRequest?: ((message: string) => Promise<void>) | null
}>()

const emit = defineEmits<{
  selectedUnitChange: [unit: SelectedUnitPayload | null]
  requestActionSheet: [unit: SelectedUnitPayload]
}>()

const selectedUnitId = ref<string | null>(null)
const isMapCollapsed = ref(false)
const isDetachedMapOpen = ref(false)
const isMapInteractive = ref(false)
const zoomScale = ref(1)
const panX = ref(0)
const panY = ref(0)
const isPanning = ref(false)
const inlineMapCanvasRef = ref<SVGSVGElement | null>(null)
const detachedMapCanvasRef = ref<SVGSVGElement | null>(null)
const inlineViewportGroupRef = ref<SVGGElement | null>(null)
const detachedViewportGroupRef = ref<SVGGElement | null>(null)
const panStartState = ref<{
  mouseX: number
  mouseY: number
  panX: number
  panY: number
  surface: 'inline' | 'detached'
} | null>(null)
const isCtrlPressed = ref(false)
const lastCtrlKeydownAt = ref(0)
const lastMapClickAt = ref(0)
const isMoveModeActive = ref(false)
const moveTarget = ref<{ x: number; y: number } | null>(null)
const isSubmittingMove = ref(false)
const isMoveDragging = ref(false)
const suppressClickUntil = ref(0)
const preservedViewport = ref<{ zoomScale: number; panX: number; panY: number } | null>(null)
const detachedMapSide = ref(520)
const MAP_DOUBLE_CLICK_THRESHOLD_MS = 500
const MOVE_DRAG_CLICK_SUPPRESS_MS = 220
const MOVE_DRAG_MIN_DISTANCE_PX = 4
const moveDragState = ref<{
  surface: 'inline' | 'detached'
  unitId: string
  startClientX: number
  startClientY: number
  moved: boolean
} | null>(null)

// 地图状态来自多路流式更新，先做宽松归一化，避免中间态直接打断整页渲染
const isRecord = (value: unknown): value is LooseRecord => {
  return typeof value === 'object' && value !== null
}

const toEntries = (value: unknown): Array<[string, any]> => {
  return isRecord(value) ? Object.entries(value) : []
}

const toNumber = (value: unknown, fallback: number): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const toOptionalNumber = (value: unknown): number | undefined => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

const toLabelText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number') return String(value)
  return fallback
}

const normalizeConditions = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []

  return value.map((condition) => {
    if (typeof condition === 'string' || typeof condition === 'number') {
      return String(condition)
    }
    if (isRecord(condition)) {
      return toLabelText(condition.name_cn ?? condition.id, '?')
    }
    return '?'
  })
}

const activeMap = computed<PlaneMap | null>(() => {
  if (!isRecord(props.space)) return null

  const maps = isRecord(props.space.maps) ? props.space.maps : null
  const activeMapId = toLabelText(props.space.active_map_id, '')
  if (!maps || !activeMapId) return null

  const rawMap = maps[activeMapId]
  if (!isRecord(rawMap)) return null

  return {
    id: toLabelText(rawMap.id, activeMapId),
    name: toLabelText(rawMap.name, activeMapId),
    width: toNumber(rawMap.width, 1),
    height: toNumber(rawMap.height, 1),
    grid_size: toOptionalNumber(rawMap.grid_size),
  }
})

const viewBoxWidth = computed(() => Math.max(1, Number(activeMap.value?.width ?? 1)))
const viewBoxHeight = computed(() => Math.max(1, Number(activeMap.value?.height ?? 1)))
const panTransform = computed(() => `translate(${panX.value} ${panY.value})`)
const viewportTransform = computed(() => {
  const centerX = viewBoxWidth.value / 2
  const centerY = viewBoxHeight.value / 2
  return [
    `translate(${centerX} ${centerY})`,
    `scale(${zoomScale.value})`,
    `translate(${-centerX} ${-centerY})`,
  ].join(' ')
})
const showFullLabels = computed(() => zoomScale.value >= 1.8)

const unitsById = computed<Record<string, UnitInfo>>(() => {
  const result: Record<string, UnitInfo> = {}

  toEntries(props.sceneUnits).forEach(([id, unit]) => {
    result[id] = isRecord(unit) ? { ...(unit as UnitInfo), id } : { id }
  })

  const participants = isRecord(props.combat) ? props.combat.participants : null
  toEntries(participants).forEach(([id, unit]) => {
    result[id] = isRecord(unit) ? { ...(unit as UnitInfo), id } : { id }
  })

  if (isRecord(props.player)) {
    const playerName = toLabelText(props.player.name, 'player')
    const playerId = toLabelText(props.player.id, `player_${playerName}`)
    result[playerId] = { id: playerId, ...props.player, side: 'player' }
  }

  toEntries(props.deadUnits).forEach(([id, unit]) => {
    const current = result[id] ?? { id }
    result[id] = isRecord(unit) ? { ...current, ...(unit as UnitInfo), id } : current
  })

  return result
})

const deadUnitIds = computed(() => {
  const ids = new Set<string>()

  toEntries(props.deadUnits).forEach(([id]) => {
    ids.add(id)
  })

  toEntries(props.sceneUnits).forEach(([id, unit]) => {
    if (isRecord(unit) && toNumber(unit.hp, 1) <= 0) ids.add(id)
  })

  const participants = isRecord(props.combat) ? props.combat.participants : null
  toEntries(participants).forEach(([id, unit]) => {
    if (isRecord(unit) && toNumber(unit.hp, 1) <= 0) ids.add(id)
  })

  if (isRecord(props.player)) {
    const playerName = toLabelText(props.player.name, 'player')
    const playerId = toLabelText(props.player.id, `player_${playerName}`)
    if (toNumber(props.player.hp, 1) <= 0) ids.add(playerId)
  }

  return ids
})

const visibleUnits = computed<VisibleUnit[]>(() => {
  const map = activeMap.value
  const placements = isRecord(props.space) && isRecord(props.space.placements) ? props.space.placements : {}
  const combatActive = isRecord(props.combat)
  if (!map) return []

  return toEntries(placements)
    .map(([unitId, rawPlacement]) => {
      const placement = isRecord(rawPlacement) ? rawPlacement : {}
      const position = isRecord(placement.position) ? placement.position : {}
      const x = toNumber(position.x, 0)
      const y = toNumber(position.y, 0)
      const info = unitsById.value[unitId] ?? { id: unitId }
      const side = typeof info.side === 'string' && info.side ? info.side : 'neutral'
      const name = toLabelText(info.name, unitId)
      const selected = unitId === selectedUnitId.value
      const isDead = deadUnitIds.value.has(unitId)

      return {
        id: unitId,
        name,
        side,
        sideClass: sideClass(side),
        initial: name.slice(0, 1).toUpperCase(),
        x,
        y,
        screenX: clamp(x, 0, map.width),
        screenY: clamp(map.height - y, 0, map.height),
        selected,
        isDead,
        hp: toOptionalNumber(info.hp),
        max_hp: toOptionalNumber(info.max_hp),
        ac: toOptionalNumber(info.ac),
        conditions: normalizeConditions(info.conditions),
      }
    })
    .filter((unit) => {
      const placement = placements[unit.id]
      if (!isRecord(placement) || toLabelText(placement.map_id, '') !== map.id) return false
      if (!combatActive && unit.side === 'enemy') return false
      return true
    })
})

const selectedUnit = computed(() => {
  return visibleUnits.value.find((unit) => unit.id === selectedUnitId.value) ?? visibleUnits.value[0] ?? null
})

// 地图内部状态只在这里转换成稳定的页面级语义事件，外层无需感知 SpaceMap 的实现细节。
watch(
  selectedUnit,
  (unit) => {
    if (!unit) {
      emit('selectedUnitChange', null)
      return
    }

    emit('selectedUnitChange', {
      id: unit.id,
      name: unit.name,
      side: unit.side,
      x: unit.x,
      y: unit.y,
      hp: unit.hp,
      maxHp: unit.max_hp,
      ac: unit.ac,
      conditions: unit.conditions,
      isDead: unit.isDead,
    })
  },
  { immediate: true },
)

const playerUnitId = computed(() => {
  if (!isRecord(props.player)) return null
  const playerName = toLabelText(props.player.name, 'player')
  return toLabelText(props.player.id, `player_${playerName}`)
})

const playerVisibleUnit = computed(() => {
  const unitId = playerUnitId.value
  if (!unitId) return null
  return visibleUnits.value.find((unit) => unit.id === unitId) ?? null
})

const currentCombatActorId = computed(() => {
  if (!isRecord(props.combat)) return ''
  return toLabelText(props.combat.current_actor_id, '')
})

const isControllableCombatTurn = computed(() => {
  if (!isCombatActive.value || !currentCombatActorId.value) return false
  if (currentCombatActorId.value === playerUnitId.value) return true
  if (!isRecord(props.combat) || !isRecord(props.combat.participants)) return false
  const actor = props.combat.participants[currentCombatActorId.value]
  return isRecord(actor) && toLabelText(actor.side, 'enemy') !== 'enemy'
})

// 复活、切图或收尾后前端可能暂时残留空 combat 对象；只有存在当前回合或参战单位时才视为真正战斗中
const isCombatActive = computed(() => {
  if (!isRecord(props.combat)) return false
  const hasCurrentActor = !!currentCombatActorId.value
  const hasParticipants = isRecord(props.combat.participants) && Object.keys(props.combat.participants).length > 0
  return hasCurrentActor || hasParticipants
})

const currentMapActor = computed(() => {
  if (!isCombatActive.value || !currentCombatActorId.value) return null
  if (currentCombatActorId.value === playerUnitId.value && isRecord(props.player)) {
    return {
      id: currentCombatActorId.value,
      name: toLabelText(props.player.name, currentCombatActorId.value),
      movement_left: toNumber(props.player.movement_left, toNumber(props.player.speed, 0)),
      speed: toNumber(props.player.speed, 0),
    }
  }
  if (!isRecord(props.combat) || !isRecord(props.combat.participants)) return null
  const actor = props.combat.participants[currentCombatActorId.value]
  if (!isRecord(actor)) return null
  return {
    id: currentCombatActorId.value,
    name: toLabelText(actor.name, currentCombatActorId.value),
    movement_left: toNumber(actor.movement_left, toNumber(actor.speed, 0)),
    speed: toNumber(actor.speed, 0),
  }
})

const moveDisabledReason = computed(() => {
  if (!activeMap.value) return '当前没有可用地图。'
  if (!playerVisibleUnit.value || !playerUnitId.value) return '地图上没有玩家落点，不能发起移动。'
  if (!props.sendTacticalMoveRequest) return '当前聊天发送器不可用，暂时无法提交移动请求。'
  if (!isCombatActive.value) return ''
  if (!currentCombatActorId.value) return '当前没有行动中的单位。'
  if (currentCombatActorId.value !== playerUnitId.value) return '战斗状态下只有你的回合才能移动。'
  if (!currentMapActor.value) return '当前行动角色数据缺失，无法校验移动。'
  const movementLeft = toNumber(currentMapActor.value.movement_left, toNumber(currentMapActor.value.speed, 0))
  if (movementLeft <= 0) return '当前剩余移动为 0，不能移动。'
  return ''
})

const moveEligibility = computed<MoveEligibility | null>(() => {
  if (moveDisabledReason.value) return null
  const playerUnit = playerVisibleUnit.value
  if (!playerUnit || !playerUnitId.value) return null

  if (!isCombatActive.value) {
    return {
      mode: 'explore',
      actorId: playerUnitId.value,
      actorName: playerUnit.name,
      fromX: playerUnit.x,
      fromY: playerUnit.y,
    }
  }

  const actor = currentMapActor.value
  if (!actor) return null

  return {
    mode: 'combat',
    actorId: playerUnitId.value,
    actorName: actor.name,
    movementLeft: actor.movement_left,
    fromX: playerUnit.x,
    fromY: playerUnit.y,
  }
})

const canEnterMoveMode = computed(() => !!moveEligibility.value)
const moveActionTitle = computed(() => {
  if (canEnterMoveMode.value) {
    if (moveEligibility.value?.mode === 'explore') {
      return isMoveModeActive.value ? '关闭探索状态' : '开启探索状态'
    }
    return isMoveModeActive.value ? '关闭战斗状态' : '开启战斗状态'
  }
  return moveDisabledReason.value || '当前不能移动'
})

const moveDistance = computed(() => {
  if (!moveEligibility.value || !moveTarget.value) return null
  const deltaX = moveTarget.value.x - moveEligibility.value.fromX
  const deltaY = moveTarget.value.y - moveEligibility.value.fromY
  return Math.hypot(deltaX, deltaY)
})

const isMoveDistanceExceeded = computed(() => {
  if (moveDistance.value === null || !moveEligibility.value) return false
  if (moveEligibility.value.mode !== 'combat') return false
  return moveDistance.value > (moveEligibility.value.movementLeft ?? 0)
})

const canConfirmMove = computed(() => {
  return !!moveEligibility.value
    && !!moveTarget.value
    && moveDistance.value !== null
    && moveDistance.value > 0
    && !isMoveDistanceExceeded.value
    && !isSubmittingMove.value
})

const moveValidationText = computed(() => {
  return isMoveAvailable.value ? '可移动' : '不可移动'
})

const movePreview = computed(() => {
  if (!moveEligibility.value || !moveTarget.value || !activeMap.value) return null
  return {
    fromScreenX: clamp(moveEligibility.value.fromX, 0, activeMap.value.width),
    fromScreenY: clamp(activeMap.value.height - moveEligibility.value.fromY, 0, activeMap.value.height),
    toScreenX: clamp(moveTarget.value.x, 0, activeMap.value.width),
    toScreenY: clamp(activeMap.value.height - moveTarget.value.y, 0, activeMap.value.height),
  }
})

const movePanelTitle = computed(() => {
  return moveEligibility.value?.mode === 'explore' ? '探索状态' : '战斗状态'
})

const moveHintText = computed(() => {
  return moveEligibility.value?.mode === 'explore'
    ? '探索状态：点击或拖拽主角选择位置；按住 Ctrl + 鼠标拖动画布'
    : '战斗状态：点击或拖拽主角设置目标；按住 Ctrl + 鼠标拖动画布'
})

const moveActorLabel = computed(() => {
  return moveEligibility.value?.mode === 'explore' ? '当前角色' : '当前行动角色'
})

const moveResourceLabel = computed(() => {
  return moveEligibility.value?.mode === 'explore' ? '状态类型' : '剩余移动'
})

const moveResourceText = computed(() => {
  if (!moveEligibility.value) return '?'
  if (moveEligibility.value.mode === 'explore') return '自由探索'
  return `${formatNumber(moveEligibility.value.movementLeft)} 尺`
})

const isMoveAvailable = computed(() => {
  if (!moveEligibility.value || !moveTarget.value || moveDistance.value === null) return false
  if (moveDistance.value <= 0) return false
  return !isMoveDistanceExceeded.value
})

const gridLines = computed(() => {
  const map = activeMap.value
  if (!map) return []

  const step = Math.max(5, Number(map.grid_size || 5))
  const lines: Array<{ key: string; path: string }> = []

  for (let x = step; x < map.width; x += step) {
    lines.push({ key: `x-${x}`, path: `M ${x} 0 L ${x} ${map.height}` })
  }
  for (let y = step; y < map.height; y += step) {
    lines.push({ key: `y-${y}`, path: `M 0 ${y} L ${map.width} ${y}` })
  }

  return lines
})

watch(visibleUnits, (units) => {
  if (selectedUnitId.value && units.some((unit) => unit.id === selectedUnitId.value)) return
  selectedUnitId.value = units[0]?.id ?? null
}, { immediate: true })

watch(activeMap, () => {
  resetViewport()
  preservedViewport.value = null
  isMapInteractive.value = false
  isMapCollapsed.value = false
  isDetachedMapOpen.value = false
  clearMovePreview()
  isMoveModeActive.value = false
})

watch(zoomScale, () => {
  clampPanIntoBounds()
})

watch(moveDisabledReason, (reason) => {
  if (!reason) return
  isMoveModeActive.value = false
  clearMovePreview()
})

watch(playerVisibleUnit, () => {
  if (!isMoveModeActive.value) return
  clearMovePreview()
})

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

const maxPanX = computed(() => Math.max(0, (viewBoxWidth.value * (zoomScale.value - 1)) / 2))
const maxPanY = computed(() => Math.max(0, (viewBoxHeight.value * (zoomScale.value - 1)) / 2))

const getSurfaceElements = (surface: 'inline' | 'detached') => {
  return surface === 'detached'
    ? {
        svg: detachedMapCanvasRef.value,
        viewportGroup: detachedViewportGroupRef.value,
      }
    : {
        svg: inlineMapCanvasRef.value,
        viewportGroup: inlineViewportGroupRef.value,
      }
}

// 用 SVG 实际变换矩阵做坐标反解，避免 CSS 尺寸、留白和 viewBox 比例不一致时出现边缘偏移
const getWorldPointFromMouse = (event: MouseEvent, surface: 'inline' | 'detached') => {
  const { svg, viewportGroup } = getSurfaceElements(surface)
  if (!svg || !viewportGroup) return null

  const ctm = viewportGroup.getScreenCTM()
  if (!ctm) return null

  const inverseMatrix = ctm.inverse()
  const point = svg.createSVGPoint()
  point.x = event.clientX
  point.y = event.clientY

  return point.matrixTransform(inverseMatrix)
}

const clampPanIntoBounds = () => {
  panX.value = clamp(Number(panX.value.toFixed(2)), -maxPanX.value, maxPanX.value)
  panY.value = clamp(Number(panY.value.toFixed(2)), -maxPanY.value, maxPanY.value)
}

const resetViewport = () => {
  zoomScale.value = 1
  panX.value = 0
  panY.value = 0
  isPanning.value = false
  panStartState.value = null
}

// 独立地图尺寸跟随聊天区宽度，维持接近聊天窗 0.7 的正方形观感
const syncDetachedMapSize = () => {
  if (typeof window === 'undefined') return
  const chatMain = document.querySelector('.chat-main') as HTMLElement | null
  const chatWidth = chatMain?.clientWidth ?? window.innerWidth
  const fallbackBase = Math.min(window.innerWidth, window.innerHeight)
  const side = Math.round((chatMain ? chatWidth : fallbackBase) * 0.7)
  detachedMapSide.value = clamp(side, 320, Math.max(320, window.innerHeight - 96))
}

const preserveViewport = () => {
  preservedViewport.value = {
    zoomScale: zoomScale.value,
    panX: panX.value,
    panY: panY.value,
  }
}

const restorePreservedViewport = () => {
  if (!preservedViewport.value) return
  zoomScale.value = preservedViewport.value.zoomScale
  panX.value = preservedViewport.value.panX
  panY.value = preservedViewport.value.panY
  clampPanIntoBounds()
}

const clearMovePreview = () => {
  stopMoveTargetDrag(false)
  moveTarget.value = null
}

const openDetachedMap = () => {
  if (!activeMap.value) return
  isDetachedMapOpen.value = true
  isMapInteractive.value = true
  syncDetachedMapSize()
  restorePreservedViewport()
}

const closeDetachedMap = () => {
  preserveViewport()
  isDetachedMapOpen.value = false
  isMapInteractive.value = false
  isPanning.value = false
  isMoveModeActive.value = false
  clearMovePreview()
}

const toggleMoveMode = () => {
  if (!canEnterMoveMode.value) return
  isMoveModeActive.value = !isMoveModeActive.value
  if (!isMoveModeActive.value) {
    clearMovePreview()
    return
  }
  isMapInteractive.value = true
  restorePreservedViewport()
  if (playerVisibleUnit.value) {
    selectedUnitId.value = playerVisibleUnit.value.id
  }
}

const activateMap = (event?: MouseEvent, surface: 'inline' | 'detached' = 'inline') => {
  const now = Date.now()
  if (now <= suppressClickUntil.value) return
  if (event?.ctrlKey || isCtrlPressed.value || now - lastCtrlKeydownAt.value <= 560 || isPanning.value) {
    return
  }
  if (surface === 'inline') {
    const interval = now - lastMapClickAt.value
    lastMapClickAt.value = now
    if (interval > 0 && interval < MAP_DOUBLE_CLICK_THRESHOLD_MS) {
      openDetachedMap()
      return
    }
  }
  if (isMoveModeActive.value && event) {
    const point = getMapPointFromMouse(event, surface)
    if (point) {
      moveTarget.value = point
    }
    return
  }
  if (isMapInteractive.value) {
    preserveViewport()
    isMapInteractive.value = false
    isMoveModeActive.value = false
    clearMovePreview()
    return
  }
  isMapInteractive.value = true
  restorePreservedViewport()
}

const toggleMapVisibility = () => {
  isMapCollapsed.value = !isMapCollapsed.value
  if (isMapCollapsed.value) {
    preserveViewport()
    isMapInteractive.value = false
    isPanning.value = false
    isMoveModeActive.value = false
    clearMovePreview()
    isDetachedMapOpen.value = false
  }
}

const handleMapWheel = (event: WheelEvent, surface: 'inline' | 'detached' = 'inline') => {
  if (surface === 'inline' && isDetachedMapOpen.value) return
  if (!isMapInteractive.value) return
  event.preventDefault()
  const nextScale = zoomScale.value + (event.deltaY < 0 ? 0.2 : -0.2)
  zoomScale.value = clamp(Number(nextScale.toFixed(2)), 1, 3)
  clampPanIntoBounds()
}

const handlePanStart = (event: MouseEvent, surface: 'inline' | 'detached' = 'inline') => {
  const mapCanvasRef = surface === 'detached' ? detachedMapCanvasRef.value : inlineMapCanvasRef.value
  if (surface === 'inline' && isDetachedMapOpen.value) return
  if (!isMapInteractive.value || !event.ctrlKey || !mapCanvasRef) return
  if (zoomScale.value <= 1) return
  event.preventDefault()
  event.stopPropagation()
  isPanning.value = true
  panStartState.value = {
    mouseX: event.clientX,
    mouseY: event.clientY,
    panX: panX.value,
    panY: panY.value,
    surface,
  }
  window.addEventListener('mousemove', handlePanMove)
  window.addEventListener('mouseup', handlePanEnd)
}

const handlePanMove = (event: MouseEvent) => {
  if (!isPanning.value || !panStartState.value) return

  const startWorldPoint = getWorldPointFromMouse({
    ...event,
    clientX: panStartState.value.mouseX,
    clientY: panStartState.value.mouseY,
  } as MouseEvent, panStartState.value.surface)
  const currentWorldPoint = getWorldPointFromMouse(event, panStartState.value.surface)
  if (!startWorldPoint || !currentWorldPoint) return

  const deltaX = currentWorldPoint.x - startWorldPoint.x
  const deltaY = currentWorldPoint.y - startWorldPoint.y

  panX.value = Number((panStartState.value.panX + deltaX).toFixed(2))
  panY.value = Number((panStartState.value.panY + deltaY).toFixed(2))
  clampPanIntoBounds()
}

const handlePanEnd = () => {
  isPanning.value = false
  panStartState.value = null
  window.removeEventListener('mousemove', handlePanMove)
  window.removeEventListener('mouseup', handlePanEnd)
}

const startMoveTargetDrag = (event: MouseEvent, surface: 'inline' | 'detached') => {
  if (!isMoveModeActive.value || event.ctrlKey || event.button !== 0) return
  if (surface === 'inline' && isDetachedMapOpen.value) return

  event.preventDefault()
  event.stopPropagation()
  if (playerUnitId.value) {
    selectedUnitId.value = playerUnitId.value
  }
  isMoveDragging.value = true
  moveDragState.value = {
    surface,
    unitId: playerUnitId.value ?? '',
    startClientX: event.clientX,
    startClientY: event.clientY,
    moved: false,
  }

  const point = getMapPointFromMouse(event, surface)
  if (point) {
    moveTarget.value = point
  }

  window.addEventListener('mousemove', handleMoveTargetDrag)
  window.addEventListener('mouseup', handleMoveTargetDragEnd)
}

// 中文注释：移动模式下只允许拖拽主角节点来选择终点，避免与查看敌人信息等点击语义混在一起。
const handleUnitDragStart = (event: MouseEvent, unitId: string, surface: 'inline' | 'detached') => {
  if (unitId !== playerUnitId.value) return
  startMoveTargetDrag(event, surface)
}

const handleMoveTargetDrag = (event: MouseEvent) => {
  if (!isMoveDragging.value || !moveDragState.value) return

  const deltaX = event.clientX - moveDragState.value.startClientX
  const deltaY = event.clientY - moveDragState.value.startClientY
  if (!moveDragState.value.moved && Math.hypot(deltaX, deltaY) >= MOVE_DRAG_MIN_DISTANCE_PX) {
    moveDragState.value.moved = true
  }

  const point = getMapPointFromMouse(event, moveDragState.value.surface)
  if (!point) return
  moveTarget.value = point
}

const stopMoveTargetDrag = (suppressClick: boolean) => {
  if (suppressClick) {
    suppressClickUntil.value = Date.now() + MOVE_DRAG_CLICK_SUPPRESS_MS
  }
  isMoveDragging.value = false
  moveDragState.value = null
  window.removeEventListener('mousemove', handleMoveTargetDrag)
  window.removeEventListener('mouseup', handleMoveTargetDragEnd)
}

const handleMoveTargetDragEnd = () => {
  const shouldSuppressClick = !!moveDragState.value?.moved
  stopMoveTargetDrag(shouldSuppressClick)
}

// 中文注释：目标点本身也允许继续拖动，保证用户在落点已出现后还能连续微调。
const handleMoveTargetHandleDragStart = (event: MouseEvent, surface: 'inline' | 'detached') => {
  if (!moveTarget.value) return
  startMoveTargetDrag(event, surface)
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key !== 'Control') return
  isCtrlPressed.value = true
  lastCtrlKeydownAt.value = Date.now()
}

const handleKeyUp = (event: KeyboardEvent) => {
  if (event.key !== 'Control') return
  isCtrlPressed.value = false
}

onMounted(() => {
  syncDetachedMapSize()
  window.addEventListener('keydown', handleKeyDown)
  window.addEventListener('keyup', handleKeyUp)
  window.addEventListener('resize', syncDetachedMapSize)
})

onBeforeUnmount(() => {
  handlePanEnd()
  stopMoveTargetDrag(false)
  window.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('keyup', handleKeyUp)
  window.removeEventListener('resize', syncDetachedMapSize)
})

const handleUnitClick = (unitId: string) => {
  if (Date.now() <= suppressClickUntil.value) return
  selectedUnitId.value = unitId
}

const handleUnitDoubleClick = (unitId: string) => {
  if (Date.now() <= suppressClickUntil.value) return
  selectedUnitId.value = unitId

  if (unitId === playerUnitId.value && canEnterMoveMode.value) {
    toggleMoveMode()
    return
  }

  if (!isControllableCombatTurn.value) return

  const unit = visibleUnits.value.find((item) => item.id === unitId)
  if (!unit || unit.side !== 'enemy') return

  // 中文注释：双击敌人只负责把“开战斗动作面板”这一意图上抛，具体动作仍由左侧战斗栏裁定。
  emit('requestActionSheet', {
    id: unit.id,
    name: unit.name,
    side: unit.side,
    x: unit.x,
    y: unit.y,
    hp: unit.hp,
    maxHp: unit.max_hp,
    ac: unit.ac,
    conditions: unit.conditions,
    isDead: unit.isDead,
  })
}

const getMapPointFromMouse = (event: MouseEvent, surface: 'inline' | 'detached' = 'inline') => {
  const map = activeMap.value
  if (!map) return null

  const worldPoint = getWorldPointFromMouse(event, surface)
  if (!worldPoint) return null

  return {
    x: Number(clamp(worldPoint.x, 0, map.width).toFixed(1)),
    y: Number(clamp(map.height - worldPoint.y, 0, map.height).toFixed(1)),
  }
}

const focusPlayer = () => {
  const unit = playerVisibleUnit.value
  if (!unit) return

  selectedUnitId.value = unit.id
  isMapInteractive.value = true

  if (preservedViewport.value) {
    restorePreservedViewport()
  } else if (zoomScale.value <= 1) {
    panX.value = 0
    panY.value = 0
    return
  }

  const centerX = viewBoxWidth.value / 2
  const centerY = viewBoxHeight.value / 2
  panX.value = (centerX - unit.screenX) * (zoomScale.value - 1)
  panY.value = (centerY - unit.screenY) * (zoomScale.value - 1)
  clampPanIntoBounds()
}

const submitMoveRequest = async () => {
  if (!moveEligibility.value || !moveTarget.value || moveDistance.value === null || !props.sendTacticalMoveRequest) return
  if (moveDistance.value <= 0 || isMoveDistanceExceeded.value) return

  const message = moveEligibility.value.mode === 'explore'
    ? [
        '【探索状态移动请求】',
        `角色：${moveEligibility.value.actorName}（${moveEligibility.value.actorId}）`,
        `地图：${activeMap.value?.name ?? '?'}（${activeMap.value?.id ?? '?'}）`,
        `起点：(${formatNumber(moveEligibility.value.fromX)}, ${formatNumber(moveEligibility.value.fromY)})`,
        `终点：(${formatNumber(moveTarget.value.x)}, ${formatNumber(moveTarget.value.y)})`,
        `直线距离：${formatNumber(moveDistance.value)} 尺`,
        '玩家意图：在非战斗探索中调整自己在当前地图上的位置。',
        '请按探索状态移动处理；这是非战斗移动，不要扣减战斗中的剩余移动，不要要求当前回合。',
        '若当前位置需要直接更新到新落点，请优先使用 manage_space 的 place_unit。',
      ].join('\n')
    : [
        '【战斗状态移动请求】',
        `行动角色：${moveEligibility.value.actorName}（${moveEligibility.value.actorId}）`,
        `当前回合角色：${currentCombatActorId.value}`,
        `地图：${activeMap.value?.name ?? '?'}（${activeMap.value?.id ?? '?'}）`,
        `起点：(${formatNumber(moveEligibility.value.fromX)}, ${formatNumber(moveEligibility.value.fromY)})`,
        `终点：(${formatNumber(moveTarget.value.x)}, ${formatNumber(moveTarget.value.y)})`,
        `预计移动距离：${formatNumber(moveDistance.value)} 尺`,
        `当前剩余移动：${formatNumber(moveEligibility.value.movementLeft)} 尺`,
        '玩家意图：尝试将自己的节点从起点移动到终点。',
      ].join('\n')

  isSubmittingMove.value = true
  try {
    await props.sendTacticalMoveRequest(message)
    isMoveModeActive.value = false
    clearMovePreview()
  } finally {
    isSubmittingMove.value = false
  }
}

const formatNumber = (value: number | undefined) => {
  if (value === undefined || Number.isNaN(value)) return '?'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

const sideLabel = (side: string) => {
  if (side === 'player') return '玩家'
  if (side === 'ally') return '友方'
  if (side === 'enemy') return '敌方'
  return '中立'
}

const sideClass = (side: string) => {
  if (side === 'player') return 'side-player'
  if (side === 'ally') return 'side-ally'
  if (side === 'enemy') return 'side-enemy'
  return 'side-neutral'
}
</script>

<style scoped>
.space-map {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 0 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.map-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(20, 20, 25, 0.58);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow:
    0 10px 24px rgba(0, 0, 0, 0.18),
    0 0 0 1px rgba(255, 255, 255, 0.02) inset;
}

.map-header.collapsed {
  padding-bottom: 10px;
}

.map-heading {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.map-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.detached-map-actions {
  padding: 0;
  border-radius: 0;
  background: transparent;
  border: none;
}

.move-toggle-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0.5px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.section-title {
  color: #d8bc8f;
  font-size: 14px;
  font-weight: 600;
  line-height: 1;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'Georgia', serif;
  letter-spacing: 0.8px;
  text-transform: uppercase;
}

.map-subtitle {
  color: #b6b7bf;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
}

.map-name {
  color: #ece7dc;
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.map-size {
  color: #9496a3;
  white-space: nowrap;
}

.map-collapsed-label {
  color: #8e8e93;
  font-size: 12px;
  letter-spacing: 0.2px;
}

.focus-player-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0.5px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.map-toggle-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0.5px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.detached-map-close-btn {
  min-width: 54px;
  height: 30px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0.5px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.05);
  color: #e5e7eb;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.move-toggle-btn:hover:not(:disabled),
.focus-player-btn:hover:not(:disabled),
.map-toggle-btn:hover:not(:disabled),
.detached-map-close-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(210, 180, 140, 0.34);
  color: #f1e3c4;
  box-shadow: 0 0 12px rgba(210, 180, 140, 0.12);
  transform: translateY(-1px);
}

.map-toggle-btn:disabled,
.focus-player-btn:disabled,
.move-toggle-btn:disabled,
.detached-map-close-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.map-shell {
  width: 100%;
}

.detached-map-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(5, 8, 14, 0.72);
  backdrop-filter: blur(10px);
}

.detached-map-panel {
  width: min(var(--detached-map-side), calc(100vw - 48px));
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: 18px;
  background:
    linear-gradient(180deg, rgba(28, 31, 39, 0.96) 0%, rgba(18, 21, 28, 0.98) 100%);
  border: 1px solid rgba(250, 204, 21, 0.2);
  box-shadow:
    0 24px 72px rgba(0, 0, 0, 0.48),
    0 0 0 1px rgba(255, 255, 255, 0.04) inset;
  overflow: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.detached-map-panel::-webkit-scrollbar {
  display: none;
}

.detached-map-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 2px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.detached-map-heading {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detached-map-title {
  font-size: 14px;
  letter-spacing: 1px;
}

.detached-map-shell {
  flex-shrink: 0;
}

.detached-stats-overlay {
  position: fixed;
  z-index: 1201;
  top: 50%;
  left: calc(50% + (min(var(--detached-map-side), calc(100vw - 48px)) / 2) + 20px);
  transform: translateY(-50%);
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 48px);
  pointer-events: none;
}

.detached-stats-panel {
  width: min(300px, calc(100vw - 48px));
  max-height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: 16px;
  background:
    linear-gradient(180deg, rgba(28, 31, 39, 0.96) 0%, rgba(18, 21, 28, 0.98) 100%);
  border: 1px solid rgba(250, 204, 21, 0.18);
  box-shadow:
    0 20px 56px rgba(0, 0, 0, 0.42),
    0 0 0 1px rgba(255, 255, 255, 0.04) inset;
  overflow: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  pointer-events: auto;
}

.detached-stats-panel::-webkit-scrollbar {
  display: none;
}

.detached-stats-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.map-shell.interactive {
  cursor: zoom-in;
}

.map-shell.interactive .map-canvas {
  border-color: rgba(250, 204, 21, 0.45);
  box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.12) inset;
}

.map-shell.move-mode .map-canvas {
  border-color: rgba(250, 204, 21, 0.82);
  box-shadow:
    0 0 0 1px rgba(250, 204, 21, 0.24) inset,
    0 0 18px rgba(250, 204, 21, 0.18);
  animation: map-golden-breathe 1.8s ease-in-out infinite;
}

.map-shell.panning,
.map-shell.panning .map-canvas {
  cursor: grabbing;
}

.map-shell.move-mode {
  cursor: crosshair;
}

.map-shell.move-mode.panning,
.map-shell.move-mode.panning .map-canvas {
  cursor: grabbing;
}

.map-canvas {
  width: 100%;
  aspect-ratio: 1 / 1;
  max-height: 260px;
  display: block;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: #17181d;
}

.detached-map-canvas {
  max-height: none;
  height: auto;
  min-height: min(var(--detached-map-side), calc(100vh - 220px));
}

.map-bg {
  fill: #191b21;
}

.grid-line {
  fill: none;
  stroke: rgba(255, 255, 255, 0.08);
  stroke-width: 0.55;
  vector-effect: non-scaling-stroke;
}

.unit-node {
  cursor: pointer;
  outline: none;
}

.unit-node.movable .unit-aura {
  stroke: rgba(56, 189, 248, 0.9);
}

.unit-aura {
  fill: rgba(255, 255, 255, 0.08);
  stroke: rgba(255, 255, 255, 0.25);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.unit-dot {
  stroke: rgba(0, 0, 0, 0.65);
  stroke-width: 0.7;
  vector-effect: non-scaling-stroke;
}

.unit-label {
  fill: #f8fafc;
  font-size: 4px;
  font-weight: 700;
  text-anchor: middle;
  paint-order: stroke;
  stroke: rgba(0, 0, 0, 0.75);
  stroke-width: 1.3;
  pointer-events: none;
}

.unit-label.expanded {
  font-size: 3.1px;
}

.move-preview-line {
  stroke: rgba(250, 204, 21, 0.95);
  stroke-width: 0.9;
  stroke-dasharray: 2 1.4;
  vector-effect: non-scaling-stroke;
}

.move-preview-target {
  fill: rgba(56, 189, 248, 0.95);
  stroke: rgba(255, 255, 255, 0.72);
  stroke-width: 0.7;
  vector-effect: non-scaling-stroke;
}

.move-preview-target.invalid {
  fill: rgba(239, 68, 68, 0.92);
}

.unit-node.selected .unit-aura {
  fill: rgba(250, 204, 21, 0.18);
  stroke: #facc15;
}

.side-player .unit-dot {
  fill: #38bdf8;
}

.side-ally .unit-dot {
  fill: #22c55e;
}

.side-enemy .unit-dot {
  fill: #ef4444;
}

.side-neutral .unit-dot {
  fill: #a78bfa;
}

.unit-node .unit-aura {
  opacity: 0.9;
}

.unit-node.is-dead .unit-dot,
.unit-node.is-dead.side-player .unit-dot,
.unit-node.is-dead.side-ally .unit-dot,
.unit-node.is-dead.side-enemy .unit-dot,
.unit-node.is-dead.side-neutral .unit-dot {
  fill: #6b7280;
}

.unit-node.is-dead .unit-aura {
  fill: rgba(107, 114, 128, 0.12);
  stroke: rgba(148, 163, 184, 0.42);
}

.unit-node.is-dead .unit-label {
  fill: #d4d4d8;
}

.axis-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #71717a;
  font-size: 11px;
  margin-top: 4px;
}

.detached-axis-row {
  margin-top: 8px;
}

.zoom-indicator {
  flex: 1;
  text-align: center;
  color: #a1a1aa;
}

.unit-detail {
  background: rgba(0, 0, 0, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 10px;
}

.move-panel {
  background: rgba(0, 0, 0, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 10px;
}

.detached-map-info {
  flex-shrink: 0;
}

.detached-stats-grid {
  grid-template-columns: minmax(0, 1fr);
}

.detached-stats-actions {
  flex-direction: column;
}

.detached-stats-actions .move-action-btn {
  width: 100%;
}

@media (max-width: 1100px) {
  .detached-stats-overlay {
    top: auto;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
  }

  .detached-stats-panel {
    width: min(420px, calc(100vw - 48px));
  }

  .detached-stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .detached-stats-actions {
    flex-direction: row;
  }

  .detached-stats-actions .move-action-btn {
    width: auto;
  }
}

.move-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.move-panel-title {
  color: #f4f4f5;
  font-size: 14px;
  font-weight: 700;
}

.move-panel-status {
  color: #8e8e93;
  font-size: 11px;
}

.move-panel-status.active {
  color: #facc15;
}

.move-panel-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.move-panel-grid div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.move-panel-grid span {
  color: #8e8e93;
  font-size: 11px;
}

.move-panel-grid strong {
  color: #e5e7eb;
  font-size: 13px;
  font-weight: 600;
}

.move-panel-grid strong.danger {
  color: #fda4af;
}

.move-panel-grid strong.movable-glow {
  color: #f3deb4;
  animation: move-available-breathe 2s ease-in-out infinite;
}

.move-panel-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.move-action-btn {
  min-height: 32px;
  padding: 0 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.04);
  color: #e5e7eb;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.18s ease;
}

.move-action-btn.subtle {
  color: #cbd5e1;
}

.move-action-btn.primary {
  color: #111827;
  background: rgba(210, 180, 140, 0.9);
  border-color: rgba(210, 180, 140, 0.45);
}

.move-action-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: rgba(210, 180, 140, 0.32);
}

.move-action-btn.primary:hover:not(:disabled) {
  background: #e6d5b8;
}

.move-action-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.move-disabled-reason {
  color: #8e8e93;
  font-size: 13px;
}

@keyframes map-golden-breathe {
  0% {
    border-color: rgba(250, 204, 21, 0.42);
    box-shadow:
      0 0 0 1px rgba(250, 204, 21, 0.14) inset,
      0 0 10px rgba(250, 204, 21, 0.08);
  }
  50% {
    border-color: rgba(250, 204, 21, 0.96);
    box-shadow:
      0 0 0 1px rgba(250, 204, 21, 0.34) inset,
      0 0 24px rgba(250, 204, 21, 0.28);
  }
  100% {
    border-color: rgba(250, 204, 21, 0.42);
    box-shadow:
      0 0 0 1px rgba(250, 204, 21, 0.14) inset,
      0 0 10px rgba(250, 204, 21, 0.08);
  }
}

.unit-detail-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.unit-name {
  color: #f4f4f5;
  font-size: 14px;
  font-weight: 700;
}

.unit-id {
  color: #71717a;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.detail-grid div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.detail-grid span {
  color: #8e8e93;
  font-size: 11px;
}

.detail-grid strong {
  color: #e5e7eb;
  font-size: 13px;
  font-weight: 600;
}

.condition-line {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

.mini-condition {
  padding: 2px 6px;
  color: #fca5a5;
  background: rgba(239, 68, 68, 0.16);
  border-radius: 6px;
  font-size: 11px;
}

.empty-map {
  color: #8e8e93;
  font-size: 13px;
  text-align: center;
  padding: 12px 0 16px;
}

@keyframes move-available-breathe {
  0% {
    color: #d9c6a0;
    text-shadow: 0 0 0 rgba(250, 204, 21, 0);
  }
  50% {
    color: #f5dfae;
    text-shadow:
      0 0 8px rgba(250, 204, 21, 0.22),
      0 0 16px rgba(250, 204, 21, 0.1);
  }
  100% {
    color: #d9c6a0;
    text-shadow: 0 0 0 rgba(250, 204, 21, 0);
  }
}
</style>
