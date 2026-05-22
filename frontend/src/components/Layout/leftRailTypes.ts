import type { AvailabilitySelectionUnit } from '../../Services_/actionAvailabilityService'
import type { PlayerState } from '../../Services_/characterStateService'

export interface CombatLeftRailState {
  player: PlayerState | null
  combat: Record<string, any> | null
  space: Record<string, any> | null
  sceneUnits?: Record<string, any> | null
  selectedUnit: AvailabilitySelectionUnit | null
  actionSheetRequestId?: number
  requestCombatActionPanel?: ((preferredTarget?: AvailabilitySelectionUnit | null) => void) | null
  sendCombatActionRequest?: ((message: string) => Promise<void>) | null
  endCombatTurnRequest?: ((actorId: string) => Promise<void>) | null
  onActionNotice?: ((text: string) => void) | null
}

export interface LeftRailState {
  mode: 'navigation' | 'combat'
  combatVisible?: boolean
  combat?: CombatLeftRailState | null
}
