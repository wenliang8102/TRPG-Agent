// frontend/src/Services_/characterStateService.ts
import { reactive, computed, ref } from 'vue'

// 类型定义（与后端保持一致）
export interface ActiveCondition {
  id: string | number
  source_id?: string
  duration?: number | null
  extra?: Record<string, any>
}

export interface WeaponData {
  name: string
  damage_dice: string
  damage_type: string
  weapon_type?: string
  properties?: string[]
}

export interface PlayerState {
  // 基础信息
  name: string
  role_class: string
  level: number

  // 生命值与防护
  hp: number
  max_hp: number
  temp_hp: number
  ac: number               // 当前总AC（含buff）
  base_ac?: number         // 无buff裸AC

  // 属性与修正
  abilities: Record<string, number>   // 原始属性值
  modifiers: Record<string, number>   // 属性修正值（可直接使用）

  // 状态与资源
  conditions: ActiveCondition[]
  resources: Record<string, number>   // 资源（如法术位、职业特性次数等）

  // 战斗相关
  weapons: WeaponData[]

  // 法术相关
  known_spells: string[]              // 已知法术（1环及以上）
  known_cantrips?: string[]           // 已知戏法
  spellcasting_ability: string        // 施法关键属性（str/dex/con/int/wis/cha）
  concentrating_on?: string | null    // 当前专注的法术名称

  // 经验与职业特性
  xp?: number                         // 经验值
  class_features?: Record<string, any> // 职业特性（字典，具体结构取决于职业）
  arcane_tradition?: string           // 奥术传统（法师学派等）
}

// 六维属性列表
export const ABILITY_LIST = [
  { key: 'str', label: '力量' },
  { key: 'dex', label: '敏捷' },
  { key: 'con', label: '体质' },
  { key: 'int', label: '智力' },
  { key: 'wis', label: '感知' },
  { key: 'cha', label: '魅力' }
] as const

// 条件名称映射
export const CONDITION_NAME_MAP: Record<string, string> = {
  blinded: '目盲',
  charmed: '魅惑',
  incapacitated: '失能',
  invisible: '隐形',
  paralyzed: '麻痹',
  petrified: '石化',
  poisoned: '中毒',
  prone: '倒地',
  restrained: '束缚',
  stunned: '震慑',
  unconscious: '昏迷',
  exhausted: '力竭',
  frightened: '恐慌',
  deafened: '耳聋',
  grappled: '擒抱',
}

/**
 * 格式化条件名称
 */
export function formatConditionName(id: string | number): string {
  const key = String(id)
  return CONDITION_NAME_MAP[key] || key
}

/**
 * 计算修正值（基于属性值）
 */
export function calcModifier(score: number): number {
  return Math.floor((score - 10) / 2)
}

/**
 * 格式化修正值显示（如 +3, -1）
 */
export function formatModifier(mod: number): string {
  return mod >= 0 ? `+${mod}` : `${mod}`
}

/**
 * 角色状态服务：管理后端同步的玩家数据，并提供变化高亮、修正值显示控制等
 */
export function useCharacterState(initialPlayer: PlayerState | null = null) {
  // 核心响应式状态
  const player = ref<PlayerState | null>(initialPlayer)

  // 高亮控制
  const hpChanged = ref(false)
  const acChanged = ref(false)
  const abilityChanged = reactive<Record<string, boolean>>({
    str: false, dex: false, con: false, int: false, wis: false, cha: false
  })

  // 修正值显示控制（默认隐藏，变化时短暂显示）
  const showModifier = reactive<Record<string, boolean>>({
    str: false, dex: false, con: false, int: false, wis: false, cha: false
  })
  const modifierChanged = reactive<Record<string, boolean>>({
    str: false, dex: false, con: false, int: false, wis: false, cha: false
  })

  // 记录上一次的状态，用于比较
  let previousPlayer: PlayerState | null = initialPlayer ? { ...initialPlayer, abilities: { ...initialPlayer.abilities }, modifiers: { ...initialPlayer.modifiers } } : null

  /**
   * 更新玩家数据（由外部调用，例如 SSE 收到 state_update 时）
   */
  function updatePlayer(newPlayer: PlayerState) {
    const oldPlayer = previousPlayer
    const newPlayerCopy = { ...newPlayer, abilities: { ...newPlayer.abilities }, modifiers: { ...newPlayer.modifiers } }

    // HP 变化高亮
    if (oldPlayer && newPlayerCopy.hp !== oldPlayer.hp) {
      hpChanged.value = true
      setTimeout(() => { hpChanged.value = false }, 800)
    }

    // AC 变化高亮
    if (oldPlayer && newPlayerCopy.ac !== oldPlayer.ac) {
      acChanged.value = true
      setTimeout(() => { acChanged.value = false }, 100)
    }

    // 属性值变化高亮（持续3秒）
    if (oldPlayer) {
      for (const ability of ABILITY_LIST) {
        const key = ability.key
        const newVal = newPlayerCopy.abilities[key] ?? 10
        const oldVal = oldPlayer.abilities[key] ?? 10
        if (newVal !== oldVal) {
          abilityChanged[key] = true
          setTimeout(() => { abilityChanged[key] = false }, 3000)
        }
      }
    }

    // 修正值变化检测（显示并高亮3秒）
    if (oldPlayer) {
      for (const ability of ABILITY_LIST) {
        const key = ability.key
        const newMod = newPlayerCopy.modifiers[key] ?? calcModifier(newPlayerCopy.abilities[key] ?? 10)
        const oldMod = oldPlayer.modifiers[key] ?? calcModifier(oldPlayer.abilities[key] ?? 10)
        if (newMod !== oldMod) {
          showModifier[key] = true
          modifierChanged[key] = true
          setTimeout(() => {
            showModifier[key] = false
            modifierChanged[key] = false
          }, 3000)
        }
      }
    }

    // 更新存储
    previousPlayer = newPlayerCopy
    player.value = newPlayerCopy
  }

  /**
   * 手动重置高亮状态（如有需要）
   */
  function resetHighlights() {
    hpChanged.value = false
    acChanged.value = false
    for (const key in abilityChanged) {
      abilityChanged[key as keyof typeof abilityChanged] = false
    }
    for (const key in modifierChanged) {
      modifierChanged[key as keyof typeof modifierChanged] = false
    }
  }

  // 计算属性：血量百分比
  const hpPercent = computed(() => {
    if (!player.value?.max_hp) return 0
    return Math.min(100, (player.value.hp / player.value.max_hp) * 100)
  })

  const tempHpPercent = computed(() => {
    if (!player.value?.max_hp || !player.value.temp_hp) return 0
    return Math.min(100, (player.value.temp_hp / player.value.max_hp) * 100)
  })

  // 获取指定能力的修正值（格式化后）
  function getModifierDisplay(key: string): string {
    if (!player.value) return ''
    const mod = player.value.modifiers?.[key]
    if (mod !== undefined) return formatModifier(mod)
    const score = player.value.abilities?.[key]
    if (score === undefined) return ''
    return formatModifier(calcModifier(score))
  }

  // 类型安全的条件列表
  const typedConditions = computed<ActiveCondition[]>(() => {
    return (player.value?.conditions || []) as ActiveCondition[]
  })

  return {
    // 状态
    player,
    hpChanged,
    acChanged,
    abilityChanged,
    showModifier,
    modifierChanged,
    // 计算属性
    hpPercent,
    tempHpPercent,
    typedConditions,
    // 方法
    updatePlayer,
    resetHighlights,
    getModifierDisplay,
    // 常量
    ABILITY_LIST,
    formatConditionName,
  }
}