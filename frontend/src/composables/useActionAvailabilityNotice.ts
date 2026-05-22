import { computed, type ComputedRef, type Ref } from 'vue'
import {
  deriveActionAvailabilityReasons,
  type ActionAvailabilityContext,
  type AvailabilityReason,
  type AvailabilitySelectionUnit,
} from '../Services_/actionAvailabilityService'
import type { PlayerState } from '../Services_/characterStateService'

/**
 * 把页面级状态收口成单一的可用性提示视图模型，页面只负责挂载展示组件。
 */
export function useActionAvailabilityNotice(
  player: Ref<PlayerState | null>,
  combat: Ref<Record<string, any> | null>,
  space: Ref<Record<string, any> | null>,
  selectedUnit: Ref<AvailabilitySelectionUnit | null>,
): {
  reasons: ComputedRef<AvailabilityReason[]>
  noticeText: ComputedRef<string>
  hasNotice: ComputedRef<boolean>
} {
  // 把聊天页分散的 player/combat/space/selectedUnit 收口成单一上下文，方便后续扩展后端动作意图字段。
  const context = computed<ActionAvailabilityContext>(() => ({
    player: player.value,
    combat: combat.value,
    space: space.value,
    selectedUnit: selectedUnit.value,
  }))

  // 提示文案的拼接规则统一留在这里，页面层只消费最终视图模型。
  const reasons = computed(() => deriveActionAvailabilityReasons(context.value))
  const noticeText = computed(() => reasons.value.map((item) => item.text).join('  ·  '))
  const hasNotice = computed(() => reasons.value.length > 0)

  return {
    reasons,
    noticeText,
    hasNotice,
  }
}
