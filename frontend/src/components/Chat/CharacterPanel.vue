<!-- frontend/src/components/Chat/CharacterPanel.vue -->
<template>
  <div class="character-panel">
    <h3>角色状态</h3>
    <div v-if="player" class="character-stats">
      <!-- 基础信息 -->
      <div class="stat-row">
        <span class="stat-label">名称</span>
        <!-- 数据来源：后端 player.name，若无则显示默认值 -->
        <span class="stat-value">{{ player.name || '无名冒险者' }}</span>
      </div>

      <!-- 生命值（带血条） -->
      <div class="stat-row">
        <span class="stat-label">生命值</span>
        <div class="hp-display">
          <!-- 数据来源：后端 player.hp / player.max_hp，hp-changed 动画由 watch 触发 -->
          <span class="stat-value" :class="{ 'hp-changed': hpChanged }">
            {{ player.hp }} / {{ player.max_hp }}
          </span>
          <div class="hp-bar">
            <!-- 血量百分比依赖 hpPercent 计算属性，该属性监听 player 变化 -->
            <div class="hp-fill" :style="{ width: hpPercent + '%' }"></div>
          </div>
        </div>
      </div>

      <!-- 护甲等级 -->
      <div class="stat-row">
        <span class="stat-label">护甲等级</span>
        <!-- 数据来源：后端 player.ac，ac-changed 动画由 watch 触发 -->
        <span class="stat-value" :class="{ 'ac-changed': acChanged }">
          {{ player.ac }}
        </span>
      </div>

      <!-- 六维属性：力量、敏捷、体质、智力、感知、魅力 -->
      <div class="abilities-section">
        <div class="section-title">属性值</div>
        <div class="abilities-grid">
          <!-- 遍历 abilityList，每个属性项独立绑定 -->
          <div v-for="ability in abilityList" :key="ability.key" class="ability-item">
            <span class="ability-name">{{ ability.label }}</span>
            <!-- 
              数据来源：后端 player.abilities[ability.key]
              例如 player.abilities.str, player.abilities.dex 等
              ability-changed 动画由 watch 中对比新旧 abilities 触发
            -->
            <span 
              class="ability-score" 
              :class="{ 'ability-changed': abilityChanged[ability.key] }"
            >
              {{ player.abilities?.[ability.key] ?? '—' }}
            </span>
            <!-- 修正值通过 formatModifier 实时计算 -->
            <span class="ability-modifier">
              {{ formatModifier(player.abilities?.[ability.key]) }}
            </span>
          </div>
        </div>
      </div>

      <!-- 可选扩展属性 -->
      <div v-if="player.level" class="stat-row">
        <span class="stat-label">等级</span>
        <span class="stat-value">{{ player.level }}</span>
      </div>
      <div v-if="player.experience" class="stat-row">
        <span class="stat-label">经验值</span>
        <span class="stat-value">{{ player.experience }}</span>
      </div>
    </div>
    <div v-else class="empty-state">
      暂无角色数据，开始对话后自动创建。
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'

// ============================================================
// 数据接收：props.player 来自父组件 Chatpages.vue
// 父组件的 playerState 由 useChatMessages 管理，数据源为：
//   1. 初始化时从后端 fetchHistory 接口获取历史角色数据
//   2. 实时聊天中通过 SSE 的 state_update 事件推送更新
// ============================================================
const props = defineProps<{
  player: any | null
}>()

// 能力值列表配置：键名必须与后端 PlayerState.abilities 字段一致
const abilityList = [
  { key: 'str', label: '力量' },
  { key: 'dex', label: '敏捷' },
  { key: 'con', label: '体质' },
  { key: 'int', label: '智力' },
  { key: 'wis', label: '感知' },
  { key: 'cha', label: '魅力' }
]

// 动画控制标志：当对应属性变化时短暂高亮
const hpChanged = ref(false)
const acChanged = ref(false)
const abilityChanged = ref<Record<string, boolean>>({})

// 用于对比新旧值的缓存变量
let previousHp: number | undefined
let previousAc: number | undefined
let previousAbilities: Record<string, number> = {}

// ============================================================
// 数据更新监听：当后端通过 SSE 推送新的 player 对象时，
// 该 watch 会被触发，对比新旧值并播放相应的变化动画。
// ============================================================
watch(() => props.player, (newPlayer, oldPlayer) => {
  if (!newPlayer) return

  // HP 变化检测（来自后端 hp 字段更新）
  if (oldPlayer && newPlayer.hp !== oldPlayer.hp) {
    hpChanged.value = true
    setTimeout(() => { hpChanged.value = false }, 800)
  }
  // AC 变化检测（来自后端 ac 字段更新）
  if (oldPlayer && newPlayer.ac !== oldPlayer.ac) {
    acChanged.value = true
    setTimeout(() => { acChanged.value = false }, 800)
  }

  // 六维属性变化检测（来自后端 abilities 对象内各字段更新）
  const newAbilities = newPlayer.abilities || {}
  const oldAbilities = oldPlayer?.abilities || {}
  for (const ability of abilityList) {
    const key = ability.key
    if (newAbilities[key] !== oldAbilities[key]) {
      abilityChanged.value[key] = true
      setTimeout(() => {
        abilityChanged.value[key] = false
      }, 600)
    }
  }

  // 更新缓存，用于下次对比
  previousHp = newPlayer.hp
  previousAc = newPlayer.ac
  previousAbilities = { ...newAbilities }
}, { deep: true, immediate: true })

// ============================================================
// 计算属性：血量百分比（依赖 player.hp 和 player.max_hp）
// 当后端推送新数据时自动重新计算，驱动血条宽度变化
// ============================================================
const hpPercent = computed(() => {
  if (!props.player || !props.player.max_hp) return 0
  return Math.min(100, (props.player.hp / props.player.max_hp) * 100)
})

// ============================================================
// 工具函数：根据 D&D 5e 规则计算能力修正值 (score-10)/2 向下取整
// 输入：能力值（例如 14），输出："+2" 或 "-1"
// 该函数不依赖外部状态，仅在模板中实时调用
// ============================================================
const formatModifier = (score: number | undefined): string => {
  if (score === undefined) return ''
  const mod = Math.floor((score - 10) / 2)
  if (mod >= 0) return `+${mod}`
  return `${mod}`
}
</script>

<style scoped>
/* 样式保持不变（略） */
.character-panel {
  padding: 16px;
  background: rgba(30, 30, 35, 0.8);
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #fff;
  height: 100%;
  overflow-y: auto;
}

h3 {
  margin-top: 0;
  margin-bottom: 16px;
  font-size: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 8px;
}

.character-stats {
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.hp-bar {
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
  margin-top: 6px;
  overflow: hidden;
}

.hp-fill {
  height: 100%;
  background: #42b883;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.abilities-section {
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
</style>