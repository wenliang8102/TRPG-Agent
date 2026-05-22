import {
  deriveActionAvailabilityReasons,
  resolvePlayerUnitId,
  type AvailabilityReasonKind,
  type AvailabilitySelectionUnit,
} from './actionAvailabilityService'
import type { InventoryItemData, PlayerState, WeaponData } from './characterStateService'
import { translateItemName, translateSpellName, translateWeaponName } from './nameTranslator'

export interface CombatActionMenuContext {
  player: PlayerState
  combat: Record<string, any> | null
  space?: Record<string, any> | null
  selectedUnit?: AvailabilitySelectionUnit | null
}

export interface CombatActionMenuItem {
  id: string
  label: string
  detail: string
  accent: 'weapon' | 'spell' | 'item' | 'class' | 'combat'
  targetMode: TargetMode
  commandPrefix: string
  command: string
  disabledReason?: string
}

export interface CombatActionMenuGroup {
  id: string
  title: string
  emptyText: string
  items: CombatActionMenuItem[]
}

type ActionUsage = 'action' | 'bonus_action' | 'free'
export type TargetMode = 'enemy' | 'ally' | 'none'

type ActionCommandTarget = Pick<AvailabilitySelectionUnit, 'name' | 'side'>

type ClassActionDefinition = {
  id: string
  label: string
  detail: string
  requiredFeatures: string[]
  usage: ActionUsage
  targetMode: TargetMode
  resourceKey?: string
}

type CombatActionDefinition = {
  id: string
  label: string
  detail: string
  usage: ActionUsage
  targetMode: TargetMode
}

const BONUS_ACTION_SPELL_IDS = new Set(['healing_word', 'misty_step', 'spiritual_weapon'])
const REACTION_SPELL_IDS = new Set(['counterspell', 'shield'])

const CLASS_ACTIONS: ClassActionDefinition[] = [
  {
    id: 'second_wind',
    label: '回气',
    detail: '附赠动作恢复生命值。',
    requiredFeatures: ['second_wind'],
    usage: 'bonus_action',
    targetMode: 'none',
    resourceKey: 'second_wind',
  },
  {
    id: 'action_surge',
    label: '动作如潮',
    detail: '立刻获得一个额外动作。',
    requiredFeatures: ['action_surge'],
    usage: 'free',
    targetMode: 'none',
    resourceKey: 'action_surge',
  },
  {
    id: 'trip_attack',
    label: '绊摔攻击',
    detail: '命中后尝试将目标击倒。',
    requiredFeatures: ['combat_superiority'],
    usage: 'action',
    targetMode: 'enemy',
    resourceKey: 'superiority_dice',
  },
  {
    id: 'rally',
    label: '鼓舞',
    detail: '附赠动作给予友方临时生命值。',
    requiredFeatures: ['combat_superiority'],
    usage: 'bonus_action',
    targetMode: 'ally',
    resourceKey: 'superiority_dice',
  },
]

const COMBAT_ACTIONS: CombatActionDefinition[] = [
  { id: 'dash', label: '疾走', detail: '本回合获得额外移动距离。', usage: 'action', targetMode: 'none' },
  { id: 'disengage', label: '脱离', detail: '本回合移动不会触发借机攻击。', usage: 'action', targetMode: 'none' },
  { id: 'dodge', label: '闪避', detail: '直到下回合前让来袭攻击更难命中。', usage: 'action', targetMode: 'none' },
  { id: 'help', label: '援助', detail: '协助队友完成攻击或检定。', usage: 'action', targetMode: 'ally' },
  { id: 'ready', label: '准备', detail: '声明触发条件并预备动作。', usage: 'action', targetMode: 'none' },
  { id: 'grapple', label: '擒抱', detail: '用近战控制一个目标。', usage: 'action', targetMode: 'enemy' },
  { id: 'shove', label: '推搡', detail: '尝试将目标推开或击倒。', usage: 'action', targetMode: 'enemy' },
]

/**
 * 中文注释：动作面板只消费前端当前能稳定读取的快照，不在这里推演任何隐藏规则。
 */
export function buildCombatActionMenu(context: CombatActionMenuContext): CombatActionMenuGroup[] {
  const playerUnitId = resolvePlayerUnitId(context.player)
  const currentActorId = normalizeText(context.combat?.current_actor_id)
  const baseReasons = deriveActionAvailabilityReasons({
    player: context.player,
    combat: context.combat,
    space: context.space ?? null,
    selectedUnit: context.selectedUnit,
  })
  const reasonMap = new Map(baseReasons.map((reason) => [reason.kind, reason.text] satisfies [AvailabilityReasonKind, string]))
  const enemyCount = countLivingEnemies(context.combat, playerUnitId)
  const classFeatures = normalizeClassFeatures(context.player.class_features)

  return [
    {
      id: 'weapon',
      title: '武器攻击',
      emptyText: '当前没有可用武器。',
      items: buildWeaponItems(context, reasonMap, enemyCount),
    },
    {
      id: 'spell',
      title: '法术',
      emptyText: '当前没有可主动施放的法术。',
      items: buildSpellItems(context, reasonMap),
    },
    {
      id: 'item',
      title: '道具',
      emptyText: '当前没有可用道具。',
      items: buildItemItems(context, reasonMap),
    },
    {
      id: 'class',
      title: '职业动作',
      emptyText: '当前没有可主动使用的职业动作。',
      items: buildClassActionItems(context, reasonMap, classFeatures, currentActorId),
    },
    {
      id: 'combat',
      title: '其他战斗动作',
      emptyText: '暂无可用战斗动作。',
      items: buildCombatActionItems(context, reasonMap, enemyCount),
    },
  ]
}

function buildWeaponItems(
  context: CombatActionMenuContext,
  reasonMap: Map<AvailabilityReasonKind, string>,
  enemyCount: number,
): CombatActionMenuItem[] {
  const weapons = context.player.weapons?.length ? context.player.weapons : [buildFallbackWeapon()]

  return weapons.map((weapon, index) => {
    const commandPrefix = `${buildActorCommandLead(context)}使用武器攻击，使用${translateWeaponName(weapon.name)}`
    return {
      id: `weapon-${weapon.name}-${index}`,
      label: translateWeaponName(weapon.name),
      detail: [weapon.damage_dice, weapon.damage_type].filter(Boolean).join(' · ') || '近战攻击',
      accent: 'weapon',
      targetMode: 'enemy',
      commandPrefix,
      command: buildCombatActionCommand(commandPrefix, 'enemy', context.selectedUnit ?? null),
      disabledReason: resolveBlockingReason({
        reasonMap,
        usage: 'action',
        targetMode: 'enemy',
        enemyCount,
        selectedUnit: context.selectedUnit,
      }),
    }
  })
}

function buildSpellItems(
  context: CombatActionMenuContext,
  reasonMap: Map<AvailabilityReasonKind, string>,
): CombatActionMenuItem[] {
  const cantrips = (context.player.known_cantrips ?? []).map((spellId) => ({ spellId, isCantrip: true }))
  const leveledSpells = (context.player.known_spells ?? []).map((spellId) => ({ spellId, isCantrip: false }))
  const spellEntries = [...cantrips, ...leveledSpells]

  return spellEntries
    .filter(({ spellId }) => !REACTION_SPELL_IDS.has(spellId))
    .map(({ spellId, isCantrip }) => {
      const translated = translateSpellName(spellId)
      const usage: ActionUsage = BONUS_ACTION_SPELL_IDS.has(spellId) ? 'bonus_action' : 'action'
      const slotBlocked = !isCantrip ? reasonMap.get('spell_slots_empty') : undefined
      const commandPrefix = `${buildActorCommandLead(context)}施放法术“${translated}”`
      return {
        id: `spell-${spellId}`,
        label: translated,
        detail: isCantrip
          ? '戏法，不消耗法术位。'
          : usage === 'bonus_action'
            ? '附赠动作法术。'
            : '标准施法。',
        accent: 'spell',
        targetMode: 'enemy',
        commandPrefix,
        command: buildCombatActionCommand(commandPrefix, 'enemy', context.selectedUnit ?? null),
        disabledReason: slotBlocked || resolveBlockingReason({
          reasonMap,
          usage,
          targetMode: 'none',
          enemyCount: countLivingEnemies(context.combat, resolvePlayerUnitId(context.player)),
          selectedUnit: context.selectedUnit,
        }),
      }
    })
}

function buildItemItems(
  context: CombatActionMenuContext,
  reasonMap: Map<AvailabilityReasonKind, string>,
): CombatActionMenuItem[] {
  const inventory = (context.player.inventory ?? []).filter(isCombatUsableItem)
  return inventory.map((item, index) => {
    const label = resolveInventoryItemLabel(item)
    const quantity = Math.max(1, item.quantity ?? 1)
    const usage = resolveItemUsage(item)
    const targetMode = resolveItemTargetMode(item)
    const commandPrefix = `${buildActorCommandLead(context)}使用道具“${label}”`
    const detailParts = [
      resolveInventoryItemKind(item),
      usage === 'bonus_action' ? '附赠动作' : '动作',
      quantity > 1 ? `数量 x${quantity}` : '单件',
      item.description?.trim() || '',
    ].filter(Boolean)

    return {
      id: `item-${item.id || label}-${index}`,
      label,
      detail: detailParts.join(' · '),
      accent: 'item',
      targetMode,
      commandPrefix,
      command: buildCombatActionCommand(commandPrefix, targetMode),
      disabledReason: resolveBlockingReason({
        reasonMap,
        usage,
        targetMode,
        enemyCount: countLivingEnemies(context.combat, resolvePlayerUnitId(context.player)),
      }),
    }
  })
}

function buildClassActionItems(
  context: CombatActionMenuContext,
  reasonMap: Map<AvailabilityReasonKind, string>,
  classFeatures: string[],
  currentActorId: string,
): CombatActionMenuItem[] {
  return CLASS_ACTIONS
    .filter((action) => action.requiredFeatures.every((featureId) => classFeatures.includes(featureId)))
    .map((action) => {
      const resourceBlocked = action.resourceKey && getResourceValue(context.player, action.resourceKey) <= 0
        ? `${action.label} 的可用次数已经耗尽。`
        : undefined
      const extraActionBlocked = action.id === 'action_surge' && context.player.extra_action_available
        ? '你已经拥有额外动作，不需要再次发动动作如潮。'
        : undefined
      const selectedTarget = action.targetMode === 'ally'
        ? context.selectedUnit
        : context.selectedUnit
      const commandPrefix = `${buildActorCommandLead(context)}使用职业动作“${action.label}”`

      return {
        id: `class-${action.id}`,
        label: action.label,
        detail: action.detail,
        accent: 'class',
        targetMode: action.targetMode,
        commandPrefix,
        command: buildCombatActionCommand(commandPrefix, action.targetMode, selectedTarget),
        disabledReason: resourceBlocked || extraActionBlocked || resolveBlockingReason({
          reasonMap,
          usage: action.usage,
          targetMode: action.targetMode,
          enemyCount: countLivingEnemies(context.combat, resolvePlayerUnitId(context.player)),
          selectedUnit: context.selectedUnit,
          currentActorId,
        }),
      }
    })
}

function buildCombatActionItems(
  context: CombatActionMenuContext,
  reasonMap: Map<AvailabilityReasonKind, string>,
  enemyCount: number,
): CombatActionMenuItem[] {
  return COMBAT_ACTIONS.map((action) => {
    const commandPrefix = `${buildActorCommandLead(context)}选择战斗动作“${action.label}”`
    return {
      id: `combat-${action.id}`,
      label: action.label,
      detail: action.detail,
      accent: 'combat',
      targetMode: action.targetMode,
      commandPrefix,
      command: buildCombatActionCommand(commandPrefix, action.targetMode, context.selectedUnit ?? null),
      disabledReason: resolveBlockingReason({
        reasonMap,
        usage: action.usage,
        targetMode: action.targetMode,
        enemyCount,
        selectedUnit: context.selectedUnit,
      }),
    }
  })
}

function resolveBlockingReason(params: {
  reasonMap: Map<AvailabilityReasonKind, string>
  usage: ActionUsage
  targetMode: TargetMode
  enemyCount: number
  selectedUnit?: AvailabilitySelectionUnit | null
  currentActorId?: string
}): string | undefined {
  const { reasonMap, usage, targetMode, enemyCount, selectedUnit } = params
  const turnBlocked = reasonMap.get('not_player_turn')
  if (turnBlocked) return turnBlocked

  if (usage === 'action') {
    const actionBlocked = reasonMap.get('action_spent')
    if (actionBlocked) return actionBlocked
  }

  if (usage === 'bonus_action') {
    const bonusBlocked = reasonMap.get('bonus_action_spent')
    if (bonusBlocked) return bonusBlocked
  }

  if (targetMode === 'enemy') {
    if (enemyCount <= 0) {
      return reasonMap.get('no_target') ?? '当前没有可用目标。'
    }
    if (selectedUnit?.side === 'enemy') {
      const rangeBlocked = reasonMap.get('target_out_of_range')
      if (rangeBlocked) return rangeBlocked
    }
  }

  if (targetMode === 'ally' && selectedUnit && !isFriendlyUnit(selectedUnit.side)) {
    return '请先在地图上选中需要配合的己方单位。'
  }

  return undefined
}

function normalizeClassFeatures(classFeatures: PlayerState['class_features']): string[] {
  if (Array.isArray(classFeatures)) {
    return classFeatures.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
  }
  if (!classFeatures || typeof classFeatures !== 'object') return []
  return Object.entries(classFeatures)
    .filter(([, value]) => Boolean(value))
    .map(([key]) => key)
}

function getResourceValue(player: PlayerState, key: string): number {
  const value = player.resources?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function countLivingEnemies(combat: Record<string, any> | null, playerUnitId: string): number {
  const participants = combat?.participants
  if (!participants || typeof participants !== 'object') return 0

  return Object.entries(participants).filter(([id, participant]) => {
    if (!participant || typeof participant !== 'object') return false
    if (id === playerUnitId) return false
    return normalizeText((participant as Record<string, any>).side) === 'enemy'
      && toNumber((participant as Record<string, any>).hp) > 0
  }).length
}

/**
 * 中文注释：命令文案统一在这里拼接目标，避免面板二次手写不同句式。
 */
export function buildCombatActionCommand(
  commandPrefix: string,
  targetMode: TargetMode,
  selectedUnit?: ActionCommandTarget | null,
): string {
  return `${commandPrefix}${buildTargetClause(selectedUnit, targetMode)}。`
}

function buildTargetClause(selectedUnit: ActionCommandTarget | null | undefined, targetMode: TargetMode): string {
  if (!selectedUnit || targetMode === 'none') return ''
  if (targetMode === 'enemy' && selectedUnit.side !== 'enemy') return ''
  if (targetMode === 'ally' && !isFriendlyUnit(selectedUnit.side)) return ''
  return `，目标是 ${selectedUnit.name}`
}

function buildActorCommandLead(context: CombatActionMenuContext): string {
  const actor = context.player as PlayerState & { id?: string; side?: string }
  const actorId = resolvePlayerUnitId(actor)
  const actorName = normalizeText(actor.name) || actorId || '当前单位'
  if (actor.side === 'ally') {
    return `我接管友方 ${actorName} [ID:${actorId}] 的回合，令其`
  }
  return '我在本回合'
}

function isFriendlyUnit(side: string): boolean {
  return side === 'player' || side === 'ally'
}

function buildFallbackWeapon(): WeaponData {
  return {
    name: 'unarmed_strike',
    damage_dice: '1',
    damage_type: '钝击',
    weapon_type: 'melee',
  }
}

function isCombatUsableItem(item: InventoryItemData): boolean {
  return item.type !== 'treasure' && Boolean(item.id || item.name || item.name_en)
}

function resolveInventoryItemLabel(item: InventoryItemData): string {
  if (item.name?.trim()) return item.name.trim()
  if (item.id?.trim()) return translateItemName(item.id)
  if (item.name_en?.trim()) return translateItemName(item.name_en)
  return '未知道具'
}

function resolveInventoryItemKind(item: InventoryItemData): string {
  if (item.type === 'potion') return '药水'
  if (item.type === 'item') return '道具'
  return translateItemName(item.id || item.name_en || item.name || 'item')
}

function resolveItemUsage(item: InventoryItemData): ActionUsage {
  return item.type === 'potion' ? 'bonus_action' : 'action'
}

function resolveItemTargetMode(item: InventoryItemData): TargetMode {
  const matcher = `${item.id} ${item.name_en ?? ''} ${item.name ?? ''}`.toLowerCase()

  // 中文注释：治疗类消耗品需要显式挑选己方目标，避免把“默认无目标”写死后挡住队友互救。
  if (
    matcher.includes('healing')
    || matcher.includes('治疗')
    || matcher.includes('vitality')
    || matcher.includes('活力')
  ) {
    return 'ally'
  }

  return 'none'
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function toNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}
