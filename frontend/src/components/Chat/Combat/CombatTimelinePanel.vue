<template>
  <aside :class="['combat-timeline-panel', { collapsed: isCollapsed }]">
    <HpOverviewPanel
      :external-player="player"
      :combat="combat"
      :space="space"
      :scene-units="sceneUnits"
      :selected-unit="selectedUnit"
      :action-sheet-request-id="actionSheetRequestId"
      :request-combat-action-panel="requestCombatActionPanel"
      @action-notice="handleActionNotice"
    />
  </aside>
</template>

<script setup lang="ts">
import HpOverviewPanel from './HpOverviewPanel.vue'
import type { AvailabilitySelectionUnit } from '../../../Services_/actionAvailabilityService'
import type { PlayerState } from '../../../Services_/characterStateService'

const props = defineProps<{
  player: PlayerState | null
  combat: Record<string, any> | null
  space: Record<string, any> | null
  sceneUnits?: Record<string, any> | null
  selectedUnit: AvailabilitySelectionUnit | null
  actionSheetRequestId?: number
  requestCombatActionPanel?: ((preferredTarget?: AvailabilitySelectionUnit | null) => void) | null
  isCollapsed?: boolean
}>()

const emit = defineEmits<{
  actionNotice: [text: string]
}>()

const handleActionNotice = (text: string) => {
  emit('actionNotice', text)
}
</script>

<style scoped>
.combat-timeline-panel {
  width: 240px;
  display: flex;
  flex-direction: column;
  height: 100vh;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  left: 0;
  padding: 0;
  background:
    linear-gradient(180deg, rgba(10, 10, 13, 0.96) 0%, rgba(16, 14, 16, 0.94) 100%);
  backdrop-filter: blur(12px);
  border-right: 1px solid rgba(255, 244, 214, 0.07);
  color: #f3eee4;
  overflow-y: auto;
  -ms-overflow-style: none;
  scrollbar-width: none;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1), padding 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.combat-timeline-panel::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.combat-timeline-panel.collapsed {
  width: 72px;
}

</style>
