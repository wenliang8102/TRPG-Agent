import type { LeftRailState } from '../components/Layout/leftRailTypes'

export const LEFT_RAIL_UPDATED_EVENT = 'trpg-left-rail-updated'
export const LEFT_RAIL_OVERRIDE_EVENT = 'trpg-left-rail-override'
export const LEFT_RAIL_MODE_EVENT = 'trpg-left-rail-mode'

/**
 * 中文注释：左侧栏状态只在前端页面壳内流转，用浏览器事件即可，不需要引入全局状态库。
 */
export function publishLeftRailState(state: LeftRailState): void {
  window.dispatchEvent(new CustomEvent<LeftRailState>(LEFT_RAIL_UPDATED_EVENT, { detail: state }))
}

export function defaultLeftRailState(): LeftRailState {
  return { mode: 'navigation', combatVisible: false, combat: null }
}

export function overrideLeftRailMode(mode: 'navigation' | 'combat' | null): void {
  window.dispatchEvent(new CustomEvent<'navigation' | 'combat' | null>(LEFT_RAIL_OVERRIDE_EVENT, { detail: mode }))
}

export function notifyLeftRailMode(mode: 'navigation' | 'combat'): void {
  window.dispatchEvent(new CustomEvent<'navigation' | 'combat'>(LEFT_RAIL_MODE_EVENT, { detail: mode }))
}
