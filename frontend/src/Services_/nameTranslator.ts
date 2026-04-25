// frontend/src/Services_/nameTranslator.ts

/**
 * 武器名称中英文映射表
 */
const weaponNameMap: Record<string, string> = {
  // 简单武器
  club: '木棒',
  dagger: '匕首',
  greatclub: '巨棒',
  handaxe: '手斧',
  javelin: '标枪',
  light_hammer: '轻锤',
  mace: '硬头锤',
  quarterstaff: '长棍',
  sickle: '镰刀',
  spear: '矛',
  light_crossbow: '轻弩',
  dart: '飞镖',
  sling: '投石索',
  // 军用武器
  battleaxe: '战斧',
  flail: '连枷',
  glaive: '大刀',
  greataxe: '巨斧',
  greatsword: '巨剑',
  halberd: '戟',
  lance: '长枪',
  longsword: '长剑',
  maul: '大锤',
  morningstar: '钉头锤',
  pike: '长矛',
  rapier: '刺剑',
  scimitar: '弯刀',
  shortsword: '短剑',
  trident: '三叉戟',
  war_pick: '战镐',
  warhammer: '战锤',
  whip: '鞭子',
  heavy_crossbow: '重弩',
  longbow: '长弓',
  shortbow: '短弓',
  // 其他常见
  unarmed_strike: '徒手打击',
  natural_weapon: '天生武器',
  bite: '啃咬',
  claw: '爪击',
}

/**
 * 法术名称中英文映射表
 */
const spellNameMap: Record<string, string> = {
  // 戏法
  fire_bolt: '火焰箭',
  light: '光亮术',
  mage_hand: '法师之手',
  prestidigitation: '魔法伎俩',
  ray_of_frost: '冷冻射线',
  shocking_grasp: '电爪',
  true_strike: '精准之击',
  acid_splash: '强酸溅射',
  blade_ward: '剑刃防护',
  dancing_lights: '舞光术',
  guidance: '神导术',
  poison_spray: '毒气喷射',
  resistance: '抵抗术',
  spare_the_dying: '稳定伤势',
  thaumaturgy: '奇术',
  // 1环
  magic_missile: '魔法飞弹',
  shield: '护盾术',
  sleep: '睡眠术',
  cure_wounds: '治疗伤口',
  bless: '祝福术',
  bane: '灾祸术',
  chromatic_orb: '七彩喷射',
  detect_magic: '侦测魔法',
  feather_fall: '羽落术',
  find_familiar: '寻获魔宠',
  fog_cloud: '云雾术',
  guiding_bolt: '导引箭',
  healing_word: '治愈真言',
  jump: '跳跃术',
  longstrider: '大步奔行',
  mage_armor: '法师护甲',
  thunderwave: '雷鸣波',
  // 2环
  hold_person: '人类定身术',
  invisibility: '隐形术',
  misty_step: '迷踪步',
  scorching_ray: '灼热射线',
  spiritual_weapon: '灵能武器',
  enhance_ability: '属性强化',
  see_invisibility: '识破隐形',
  suggestion: '暗示术',
  // 3环
  fireball: '火球术',
  lightning_bolt: '闪电束',
  counterspell: '反制法术',
  dispel_magic: '解除魔法',
  fly: '飞行术',
  haste: '加速术',
  slow: '缓慢术',
  revivify: '死者复活',
  spirit_guardians: '灵体卫士',
  hypnotic_pattern: '催眠图纹',
  // 带空格的变体
  'acid splash': '强酸溅射',
  'blade ward': '剑刃防护',
  'dancing lights': '舞光术',
  'poison spray': '毒气喷射',
  'spare the dying': '稳定伤势',
}

/**
 * 道具/物品名称中英文映射表
 */
const itemNameMap: Record<string, string> = {
  potion_of_healing: '治疗药水',
  greater_potion_of_healing: '强效治疗药水',
  superior_potion_of_healing: '高等治疗药水',
  supreme_potion_of_healing: '极效治疗药水',
  potion_of_invisibility: '隐形药水',
  potion_of_hill_giant_strength: '山丘巨人力量药水',
  potion_of_fire_breath: '火焰吐息药水',
  scroll_of_fireball: '火球术卷轴',
  scroll_of_magic_missile: '魔法飞弹卷轴',
  scroll_of_cure_wounds: '治疗伤口卷轴',
  bag_of_holding: '次元袋',
  rope_of_climbing: '攀爬绳',
  alchemists_fire: '炼金之火',
  antitoxin: '抗毒剂',
  caltrops: '蒺藜',
  holy_water: '圣水',
  oil: '油',
  poison_basic: '基本毒药',
  torch: '火把',
  rations: '口粮',
  waterskin: '水囊',
  bedroll: '铺盖',
  rope: '麻绳',
  crowbar: '撬棍',
  hammer: '锤子',
  piton: '岩钉',
  lantern: '提灯',
  tinderbox: '火绒盒',
}

/**
 * 资源名称中英文映射表（精确匹配用）
 */
const resourceNameMap: Record<string, string> = {
  // 法术位（标准下划线格式）
  spell_slots_level_1: '一环法术位',
  spell_slots_level_2: '二环法术位',
  spell_slots_level_3: '三环法术位',
  spell_slots_level_4: '四环法术位',
  spell_slots_level_5: '五环法术位',
  spell_slots_level_6: '六环法术位',
  spell_slots_level_7: '七环法术位',
  spell_slots_level_8: '八环法术位',
  spell_slots_level_9: '九环法术位',
  spell_slots_level_0: '戏法位',
  // 职业资源
  rages: '狂暴次数',
  rage: '狂暴',
  action_surge: '动作如潮',
  second_wind: '回气',
  ki_points: '气',
  sneak_attack: '偷袭',
  inspiration: '激励',
  channel_divinity: '引导神力',
  wild_shape: '荒野形态',
  superiority_dice: '卓越骰',
  bardic_inspiration: '诗人激励',
  lay_on_hands: '圣疗',
  divine_sense: '神圣感知',
  favored_foe: '宿敌',
  natural_recovery: '自然恢复',
  arcane_recovery: '奥法回复',
  sorcery_points: '术法点',
  metamagic: '超魔法',
  pact_slots: '契法术位',
  // 通用
  mana: '法力',
  stamina: '精力',
  experience: '经验值',
}

/**
 * 格式化未匹配的名称（将下划线转空格，每个单词首字母大写）
 */
function formatFallbackName(name: string): string {
  // 如果已经是纯中文或包含中文，直接返回
  if (/[\u4e00-\u9fa5]/.test(name)) return name

  // 将下划线替换为空格，首字母大写
  return name
    .replace(/_/g, ' ')
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

/**
 * 翻译武器名称
 */
export function translateWeaponName(englishName: string): string {
  const lower = englishName.toLowerCase().trim()
  return weaponNameMap[lower] || formatFallbackName(englishName)
}

/**
 * 翻译法术名称
 */
export function translateSpellName(englishName: string): string {
  const lower = englishName.toLowerCase().trim()
  return spellNameMap[lower] || formatFallbackName(englishName)
}

/**
 * 翻译道具名称
 */
export function translateItemName(englishName: string): string {
  const lower = englishName.toLowerCase().trim()
  return itemNameMap[lower] || formatFallbackName(englishName)
}

/**
 * 翻译资源名称（增强版：支持法术位的常见变体）
 */
export function translateResourceName(englishName: string): string {
  const lower = englishName.toLowerCase().trim()
  
  // 1. 精确匹配（优先）
  if (resourceNameMap[lower]) {
    return resourceNameMap[lower]
  }
  
  // 2. 智能匹配法术位：只要 key 中包含 'spell' 和 'slot' 并且包含数字
  if (lower.includes('spell') && lower.includes('slot')) {
    const match = englishName.match(/\d+/)  // 提取第一个数字
    if (match) {
      const level = parseInt(match[0], 10)
      if (level >= 0 && level <= 9) {
        const levelNames: Record<number, string> = {
          0: '戏法位', 1: '一环法术位', 2: '二环法术位', 3: '三环法术位',
          4: '四环法术位', 5: '五环法术位', 6: '六环法术位',
          7: '七环法术位', 8: '八环法术位', 9: '九环法术位'
        }
        return levelNames[level] || `${level}环法术位`
      }
    }
  }
  
  // 3. 其他常见资源（Pact Slots, Mana Points 等）
  if (lower.includes('pact') && lower.includes('slot')) return '契法术位'
  if (lower.includes('mana') && lower.includes('point')) return '法力值'
  if (lower.includes('channel') && lower.includes('divinity')) return '引导神力'
  if (lower.includes('wild') && lower.includes('shape')) return '荒野形态'
  
  // 4. 兜底格式化
  return formatFallbackName(englishName)
}

/**
 * 通用翻译，根据类型自动选择
 */
export function translateName(
  category: 'weapon' | 'spell' | 'item' | 'resource',
  name: string
): string {
  switch (category) {
    case 'weapon':
      return translateWeaponName(name)
    case 'spell':
      return translateSpellName(name)
    case 'item':
      return translateItemName(name)
    case 'resource':
      return translateResourceName(name)
    default:
      return name
  }
}