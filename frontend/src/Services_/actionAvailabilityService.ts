import type { PlayerState, WeaponData } from './characterStateService'

export type AvailabilityReasonKind =
  | 'not_player_turn'
  | 'action_spent'
  | 'bonus_action_spent'
  | 'reaction_unavailable'
  | 'spell_slots_empty'
  | 'no_target'
  | 'target_out_of_range'

export interface AvailabilityReason {
  kind: AvailabilityReasonKind
  text: string
}

export interface AvailabilitySelectionUnit {
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

export interface ActionAvailabilityContext {
  player: PlayerState | null
  combat: Record<string, any> | null
  space: Record<string, any> | null
  selectedUnit?: AvailabilitySelectionUnit | null
}

type CombatActorSnapshot = {
  id: string
  side: string
  hp: number
}

/**
 * 统一解析玩家在战斗与空间系统里的单位 ID，避免页面重复拼接后端约定。
 */
export function resolvePlayerUnitId(player: PlayerState | null): string {
  if (!player) return ''
  if (typeof player.id === 'string' && player.id.trim()) {
    return player.id.trim()
  }
  const name = typeof player.name === 'string' ? player.name.trim() : ''
  return name ? `player_${name}` : ''
}

/**
 * 前端只做保守提示：只有能从当前快照稳定推出的阻塞原因，才展示给用户。
 */
export function deriveActionAvailabilityReasons(context: ActionAvailabilityContext): AvailabilityReason[] {
  const { player, combat, space, selectedUnit } = context
  if (!player || !isCombatActive(combat)) return []

  const playerUnitId = resolvePlayerUnitId(player)
  if (!playerUnitId) return []

  const reasons: AvailabilityReason[] = []
  const currentActorId = normalizeText(combat?.current_actor_id)

  if (currentActorId && currentActorId !== playerUnitId) {
    reasons.push({ kind: 'not_player_turn', text: '当前不是你的回合，不能主动执行动作。' })
  }

  if (!hasActionAvailable(player)) {
    reasons.push({ kind: 'action_spent', text: '你的动作已用尽。' })
  }

  if (player.bonus_action_available === false) {
    reasons.push({ kind: 'bonus_action_spent', text: '你的附赠动作已用尽。' })
  }

  if (player.reaction_available === false) {
    reasons.push({ kind: 'reaction_unavailable', text: '你的反应当前不可用。' })
  }

  if (hasLeveledSpells(player) && hasNoSpellSlots(player)) {
    reasons.push({ kind: 'spell_slots_empty', text: '你的法术位已经耗尽。' })
  }

  if (currentActorId === playerUnitId) {
    const livingEnemies = collectLivingEnemies(combat, playerUnitId)
    if (livingEnemies.length === 0) {
      reasons.push({ kind: 'no_target', text: '当前没有可用目标。' })
    } else {
      const rangeReason = deriveRangeReason(player, space, selectedUnit)
      if (rangeReason) {
        reasons.push(rangeReason)
      }
    }
  }

  return dedupeReasons(reasons)
}

/**
 * 这里只判断“是否处在有效战斗态”，避免把后端残留的空 combat 壳误当成真正战斗。
 */
function isCombatActive(combat: Record<string, any> | null): combat is Record<string, any> {
  if (!combat || typeof combat !== 'object') return false
  const currentActorId = normalizeText(combat.current_actor_id)
  const participants = combat.participants
  return !!currentActorId || (!!participants && typeof participants === 'object' && Object.keys(participants).length > 0)
}

function hasActionAvailable(player: PlayerState): boolean {
  return player.action_available !== false || player.extra_action_available === true
}

function hasLeveledSpells(player: PlayerState): boolean {
  return Array.isArray(player.known_spells) && player.known_spells.length > 0
}

function hasNoSpellSlots(player: PlayerState): boolean {
  const resources = player.resources ?? {}
  const resourceKeys = Object.keys(resources).filter((key) => key.startsWith('spell_slot_lv') || key.startsWith('pact_magic_lv'))
  if (resourceKeys.length === 0) return true
  return resourceKeys.every((key) => toNumber(resources[key]) <= 0)
}

/**
 * 没有后端显式目标列表时，前端只粗筛仍然活着的敌方单位，用于“没有目标”的基础提示。
 */
function collectLivingEnemies(combat: Record<string, any>, playerUnitId: string): CombatActorSnapshot[] {
  const participants = combat.participants
  if (!participants || typeof participants !== 'object') return []

  return Object.entries(participants)
    .map(([id, value]) => {
      if (!value || typeof value !== 'object') return null
      return {
        id,
        side: normalizeText((value as Record<string, any>).side),
        hp: toNumber((value as Record<string, any>).hp),
      } satisfies CombatActorSnapshot
    })
    .filter((item): item is CombatActorSnapshot => !!item)
    .filter((item) => item.id !== playerUnitId && item.side === 'enemy' && item.hp > 0)
}

/**
 * 射程提示必须足够确定才展示，避免前端在不知道玩家具体意图时误判。
 */
function deriveRangeReason(
  player: PlayerState,
  space: Record<string, any> | null,
  selectedUnit?: AvailabilitySelectionUnit | null,
): AvailabilityReason | null {
  if (!selectedUnit || selectedUnit.side !== 'enemy' || selectedUnit.isDead) return null

  const playerUnitId = resolvePlayerUnitId(player)
  if (!playerUnitId || !space || typeof space !== 'object') return null

  const placements = space.placements
  if (!placements || typeof placements !== 'object') return null

  const playerPlacement = placements[playerUnitId]
  const playerPoint = extractPlacementPoint(playerPlacement)
  if (!playerPoint) return null

  const maxRange = getMaximumConfidentRange(player.weapons ?? [], player)
  if (maxRange === null) return null

  const distance = Math.hypot(selectedUnit.x - playerPoint.x, selectedUnit.y - playerPoint.y)
  if (distance <= maxRange) return null

  return {
    kind: 'target_out_of_range',
    text: `当前选中的目标距离约 ${formatDistance(distance)} 尺，明显超出你的直接作用范围。`,
  }
}

/**
 * 射程上限只采用前端当前能稳定读取的武器数据。
 * 一旦角色存在法术能力，但前端又拿不到明确法术射程时，就宁可少提示也不误判。
 */
function getMaximumConfidentRange(weapons: WeaponData[], player: PlayerState): number | null {
  const weaponRanges = weapons
    .map((weapon) => getWeaponEffectiveRange(weapon))
    .filter((range): range is number => range !== null)

  // 前端没有正式的法术射程目录时，只在“没有已知法术”或“有明确武器上限”时给出射程提示。
  if (player.known_spells?.length) {
    return weaponRanges.length > 0 ? Math.max(...weaponRanges) : null
  }

  if (weaponRanges.length === 0) return null
  return Math.max(...weaponRanges)
}

function getWeaponEffectiveRange(weapon: WeaponData): number | null {
  const longRange = toNumber((weapon as WeaponData & { long_range_feet?: number }).long_range_feet)
  if (longRange > 0) return longRange

  const normalRange = toNumber((weapon as WeaponData & { normal_range_feet?: number }).normal_range_feet)
  if (normalRange > 0) return normalRange

  const reach = toNumber((weapon as WeaponData & { reach_feet?: number }).reach_feet)
  if (reach > 0) return reach

  if (weapon.weapon_type === 'melee') return 5
  return null
}

function extractPlacementPoint(placement: unknown): { x: number; y: number } | null {
  if (!placement || typeof placement !== 'object') return null
  const position = (placement as Record<string, any>).position
  if (!position || typeof position !== 'object') return null
  return {
    x: toNumber((position as Record<string, any>).x),
    y: toNumber((position as Record<string, any>).y),
  }
}

function dedupeReasons(reasons: AvailabilityReason[]): AvailabilityReason[] {
  const seen = new Set<AvailabilityReasonKind>()
  return reasons.filter((reason) => {
    if (seen.has(reason.kind)) return false
    seen.add(reason.kind)
    return true
  })
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function toNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function formatDistance(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}
