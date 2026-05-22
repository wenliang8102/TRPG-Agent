<!-- frontend/src/components/Chat/SideCharacterPanel/InventoryPanel.vue -->
<template>
  <div class="inventory-panel">
    <div v-if="inventoryEntries.length" class="inventory-section">
      <div class="section-title">随身物品</div>
      <div class="inventory-list">
        <div
          v-for="entry in inventoryEntries"
          :key="entry.key"
          class="inventory-item"
        >
          <div class="item-main">
            <div class="item-name-row">
              <span class="item-name">{{ entry.name }}</span>
              <span class="item-kind">{{ entry.kind }}</span>
            </div>
            <div v-if="entry.detail" class="item-detail">{{ entry.detail }}</div>
          </div>
          <span class="item-quantity">{{ entry.quantityText }}</span>
        </div>
      </div>
    </div>

    <div v-else class="empty-state">
      暂无可展示的背包物品
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { InventoryItemData, PlayerState, WeaponData } from '../../../Services_/characterStateService'
import { translateItemName, translateWeaponName } from '../../../Services_/nameTranslator'

type InventoryEntry = {
  key: string
  name: string
  kind: string
  detail: string
  quantityText: string
}

const props = defineProps<{
  externalPlayer: PlayerState | null
}>()

const currencyOrder = ['pp', 'gp', 'ep', 'sp', 'cp']
const currencyLabels: Record<string, string> = {
  cp: 'CP',
  sp: 'SP',
  ep: 'EP',
  gp: 'GP',
  pp: 'PP',
}

// 背包条目以 id 为稳定事实源，name 缺失时仍能展示药水。
const resolveItemName = (item: InventoryItemData): string => {
  if (item.name?.trim()) return item.name
  if (item.id?.trim()) return translateItemName(item.id)
  if (item.name_en?.trim()) return translateItemName(item.name_en)
  return '未知道具'
}

// 价格是购物后的校验信息；剧情财宝没有标价时只显示描述。
const formatItemDetail = (item: InventoryItemData): string => {
  const parts = [item.description?.trim()].filter(Boolean) as string[]
  if (typeof item.price_gp === 'number') {
    parts.push(`参考价 ${item.price_gp} GP`)
  }
  return parts.join(' · ')
}

// 背包页展示角色卡里的真实持有状态：钱袋、剧情奖励物品和武器。
const inventoryEntries = computed<InventoryEntry[]>(() => {
  const player = props.externalPlayer
  if (!player) return []

  const coinEntries = currencyOrder
    .filter((currency) => (player.coins?.[currency] ?? 0) > 0)
    .map((currency) => ({
      key: `coin-${currency}`,
      name: currencyLabels[currency],
      kind: '货币',
      detail: '剧情奖励钱袋',
      quantityText: `${player.coins?.[currency] ?? 0}`,
    }))

  const rewardEntries = (player.inventory ?? [])
    .filter((item): item is InventoryItemData => Boolean(item?.id || item?.name || item?.name_en))
    .map((item, index) => ({
      key: item.id || `${item.name}-${index}`,
      name: resolveItemName(item),
      kind: item.type === 'treasure' ? '财宝' : item.type === 'potion' ? '药水' : '道具',
      detail: formatItemDetail(item),
      quantityText: `x${item.quantity ?? 1}`,
    }))

  const weaponEntries = (player.weapons ?? [])
    .filter((weapon): weapon is WeaponData => Boolean(weapon?.name))
    .map((weapon, index) => ({
      key: `${weapon.name}-${index}`,
      name: translateWeaponName(weapon.name),
      kind: '武器',
      detail: [weapon.damage_dice, weapon.damage_type].filter(Boolean).join(' '),
      quantityText: 'x1',
    }))

  return [...coinEntries, ...rewardEntries, ...weaponEntries]
})
</script>

<style scoped>
.inventory-panel {
  min-height: 220px;
}

.inventory-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #c9a87b;
  letter-spacing: 1px;
}

.inventory-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.inventory-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.24);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.item-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.item-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.item-name {
  color: #e8e5dc;
  font-size: 14px;
  font-weight: 600;
}

.item-kind {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(201, 168, 123, 0.14);
  color: #d4b58a;
  font-size: 11px;
}

.item-detail {
  color: #9ca3af;
  font-size: 12px;
}

.item-quantity {
  flex-shrink: 0;
  color: #d6d3d1;
  font-size: 13px;
  font-family: monospace;
}

.empty-state {
  color: #8e8e93;
  font-style: italic;
  text-align: center;
  padding: 20px 0;
}
</style>
