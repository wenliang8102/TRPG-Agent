<template>
  <component :is="renderInOverlay ? 'Teleport' : 'div'" v-bind="teleportProps">
    <Transition name="combat-sheet-fade">
      <div
        v-if="open"
        :class="renderInOverlay ? 'combat-sheet-overlay' : 'combat-sheet-inline'"
        @click.self="renderInOverlay ? emit('close') : undefined"
      >
        <div class="combat-sheet" :class="{ embedded: !renderInOverlay }">
          <div class="sheet-header">
            <div class="sheet-heading">
              <button
                v-if="pendingTargetItem"
                type="button"
                class="sheet-back"
                @click="resetTargetPicker"
              >
                ← 返回
              </button>
              <h4 v-if="pendingTargetItem" class="sheet-title">{{ buildTargetTitle(pendingTargetItem) }}</h4>
            </div>
          </div>

          <div class="sheet-stage-viewport">
            <Transition name="sheet-stage" mode="out-in">
              <div v-if="pendingTargetItem" key="target-picker" class="sheet-groups">
                <section class="sheet-group">
                  <div class="group-title">目标对象</div>
                  <div v-if="filteredTargetOptions.length" class="group-list">
                    <button
                      v-for="target in filteredTargetOptions"
                      :key="target.id"
                      type="button"
                      class="action-item target-item"
                      :class="`accent-${pendingTargetItem.accent}`"
                      @click="handleTargetClick(target)"
                    >
                      <span class="item-main">
                        <span class="item-label">{{ target.name }}</span>
                        <span class="item-detail">{{ resolveTargetDetail(target) }}</span>
                      </span>
                      <span class="item-state item-ready">选择</span>
                    </button>
                  </div>
                  <div v-else class="group-empty">{{ buildEmptyTargetText(pendingTargetItem) }}</div>
                </section>
              </div>

              <div v-else key="action-groups" class="sheet-groups">
                <section v-for="group in groups" :key="group.id" class="sheet-group">
                  <div class="group-title">{{ group.title }}</div>
                  <div v-if="group.items.length" class="group-list">
                    <button
                      v-for="item in group.items"
                      :key="item.id"
                      type="button"
                      class="action-item"
                      :class="[`accent-${item.accent}`, { disabled: !!item.disabledReason }]"
                      @click="handleItemClick(item)"
                    >
                      <span class="item-main">
                        <span class="item-label">{{ item.label }}</span>
                        <span class="item-detail">{{ item.detail }}</span>
                      </span>
                      <span v-if="item.disabledReason" class="item-state">{{ item.disabledReason }}</span>
                      <span v-else class="item-state item-ready">可用</span>
                    </button>
                  </div>
                  <div v-else class="group-empty">{{ group.emptyText }}</div>
                </section>
              </div>
            </Transition>
          </div>

          <div class="sheet-footer">
            <button
              type="button"
              class="end-turn-btn"
              :disabled="disabledEndTurn"
              @click="emit('endTurn')"
            >
              结束回合
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </component>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CombatActionMenuGroup, CombatActionMenuItem } from '../../../Services_/combatActionCatalog'

type CombatTargetOption = {
  id: string
  name: string
  side: string
  hp: number
  maxHp: number
}

const props = withDefaults(defineProps<{
  open: boolean
  actorName: string
  groups: CombatActionMenuGroup[]
  selectedTargetName?: string
  disabledEndTurn?: boolean
  preferredTarget?: CombatTargetOption | null
  targetOptions?: CombatTargetOption[]
  renderInOverlay?: boolean
}>(), {
  selectedTargetName: '',
  disabledEndTurn: false,
  preferredTarget: null,
  targetOptions: () => [],
  renderInOverlay: false,
})

const emit = defineEmits<{
  close: []
  submit: [item: CombatActionMenuItem]
  submitWithTarget: [payload: { item: CombatActionMenuItem; target: CombatTargetOption }]
  blocked: [reason: string]
  endTurn: []
}>()

const pendingTargetItem = ref<CombatActionMenuItem | null>(null)
const teleportProps = computed(() => props.renderInOverlay ? { to: 'body' } : {})
const filteredTargetOptions = computed(() => {
  if (!pendingTargetItem.value) return []
  if (pendingTargetItem.value.targetMode === 'enemy') {
    return props.targetOptions.filter((target) => target.side === 'enemy')
  }
  if (pendingTargetItem.value.targetMode === 'ally') {
    return props.targetOptions.filter((target) => target.side === 'player' || target.side === 'ally')
  }
  return []
})

watch(
  () => props.open,
  (open) => {
    if (!open) {
      pendingTargetItem.value = null
    }
  },
)

/**
 * 中文注释：动作面板只分流“可提交”和“不可提交”，具体规则仍以后端裁定为准。
 */
const handleItemClick = (item: CombatActionMenuItem) => {
  if (item.disabledReason) {
    emit('blocked', item.disabledReason)
    return
  }

  // 中文注释：所有显式选目标的动作统一走右侧目标页，避免武器、道具、职业动作各自分叉。
  if (item.targetMode !== 'none') {
    pendingTargetItem.value = item
    return
  }

  emit('submit', item)
}

const handleTargetClick = (target: CombatTargetOption) => {
  if (!pendingTargetItem.value) return
  emit('submitWithTarget', { item: pendingTargetItem.value, target })
  pendingTargetItem.value = null
}

const resetTargetPicker = () => {
  pendingTargetItem.value = null
}

function buildTargetTitle(item: CombatActionMenuItem): string {
  return `选择 ${item.label} 的目标`
}

function resolveTargetDetail(target: CombatTargetOption): string {
  const sideLabel = target.side === 'enemy' ? '敌方' : target.side === 'ally' ? '友方' : '玩家'
  return `${sideLabel} · HP ${target.hp} / ${target.maxHp}`
}

function buildEmptyTargetText(item: CombatActionMenuItem): string {
  if (item.targetMode === 'ally') return '当前没有可选择的我方目标。'
  if (item.targetMode === 'enemy') return '当前没有可攻击目标。'
  return '当前没有可用目标。'
}
</script>

<style scoped>
.combat-sheet-inline {
  height: 100%;
}

.combat-sheet-overlay {
  position: fixed;
  inset: 0;
  z-index: 10010;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(6, 8, 11, 0.64);
  backdrop-filter: blur(12px);
  padding: 28px;
}

.combat-sheet {
  width: min(780px, 100%);
  max-height: min(84vh, 920px);
  overflow: auto;
  -ms-overflow-style: none;
  scrollbar-width: none;
  border-radius: 28px;
  background:
    linear-gradient(180deg, rgba(24, 25, 33, 0.99) 0%, rgba(12, 13, 18, 0.99) 100%);
  border: 1px solid rgba(223, 190, 128, 0.2);
  box-shadow:
    0 30px 80px rgba(0, 0, 0, 0.48),
    0 0 0 1px rgba(255, 244, 214, 0.05) inset;
  color: #f5efe4;
}

.combat-sheet.embedded {
  width: 100%;
  max-height: none;
  border-radius: 24px;
  box-shadow:
    0 18px 48px rgba(0, 0, 0, 0.28),
    0 0 0 1px rgba(255, 244, 214, 0.05) inset;
}

.combat-sheet::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.sheet-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 24px 24px 14px;
}

.sheet-heading {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.sheet-title {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.15;
}

.sheet-back {
  width: fit-content;
  padding: 7px 12px;
  border: 1px solid rgba(214, 182, 126, 0.24);
  border-radius: 999px;
  background: rgba(214, 182, 126, 0.1);
  color: #f0d8aa;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.sheet-groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 0 24px 18px;
}

.sheet-stage-viewport {
  position: relative;
  overflow: hidden;
}

.sheet-stage-enter-active,
.sheet-stage-leave-active {
  transition: opacity 0.24s ease, transform 0.24s ease;
}

.sheet-stage-enter-from {
  opacity: 0;
  transform: translateX(26px);
}

.sheet-stage-leave-to {
  opacity: 0;
  transform: translateX(-26px);
}

.sheet-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.group-title {
  color: #cfb284;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.action-item {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  width: 100%;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.04);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.target-item {
  align-items: center;
}

.action-item:hover {
  transform: translateY(-1px);
}

.action-item.disabled {
  cursor: not-allowed;
  opacity: 0.78;
}

.item-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.item-label {
  font-size: 16px;
  font-weight: 600;
}

.item-detail {
  color: #9fa4b3;
  font-size: 13px;
}

.item-state {
  max-width: 220px;
  color: #f5c7c7;
  font-size: 13px;
  text-align: right;
}

.item-ready {
  color: #d9c08b;
}

.accent-weapon {
  border-color: rgba(176, 71, 52, 0.28);
}

.accent-spell {
  border-color: rgba(71, 120, 196, 0.28);
}

.accent-item {
  border-color: rgba(134, 94, 255, 0.26);
}

.accent-class {
  border-color: rgba(199, 159, 77, 0.28);
}

.accent-combat {
  border-color: rgba(104, 126, 86, 0.28);
}

.group-empty {
  padding: 16px 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.03);
  color: #898f9d;
  font-size: 13px;
}

.sheet-footer {
  position: sticky;
  bottom: 0;
  z-index: 2;
  padding: 16px 24px 24px;
  background:
    linear-gradient(180deg, rgba(12, 13, 18, 0) 0%, rgba(12, 13, 18, 0.86) 24%, rgba(12, 13, 18, 0.98) 100%);
  backdrop-filter: blur(8px);
}

.end-turn-btn {
  width: 100%;
  min-height: 56px;
  border: 1px solid rgba(239, 68, 68, 0.28);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(127, 29, 29, 0.96) 0%, rgba(69, 10, 10, 0.98) 100%);
  box-shadow:
    0 12px 28px rgba(127, 29, 29, 0.28),
    0 0 0 1px rgba(255, 255, 255, 0.04) inset;
  color: #ffe3e3;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.end-turn-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(248, 113, 113, 0.44);
  box-shadow:
    0 14px 30px rgba(127, 29, 29, 0.34),
    0 0 0 1px rgba(255, 255, 255, 0.06) inset;
}

.end-turn-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
  transform: none;
  box-shadow: none;
}

.end-turn-btn:active {
  transform: translateY(0);
}

.combat-sheet-fade-enter-active,
.combat-sheet-fade-leave-active {
  transition: opacity 0.22s ease;
}

.combat-sheet-fade-enter-from,
.combat-sheet-fade-leave-to {
  opacity: 0;
}

.combat-sheet-fade-enter-from .combat-sheet,
.combat-sheet-fade-leave-to .combat-sheet {
  transform: translateY(14px);
}

.combat-sheet-fade-enter-active .combat-sheet,
.combat-sheet-fade-leave-active .combat-sheet {
  transition: transform 0.22s ease;
}

@media (max-width: 640px) {
  .combat-sheet-overlay {
    padding: 12px;
  }
  .sheet-header,
  .sheet-groups,
  .sheet-footer {
    padding-left: 16px;
    padding-right: 16px;
  }
  .sheet-target {
    margin-left: 16px;
    margin-right: 16px;
  }
  .action-item {
    flex-direction: column;
    align-items: flex-start;
  }
  .item-state {
    max-width: none;
    text-align: left;
  }
}
</style>
