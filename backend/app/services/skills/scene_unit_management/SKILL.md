# 场景单位管理技能

当需要把友方、怪物或 NPC 放入当前场景单位池，或在战斗后清理死亡单位档案时，使用本技能。场景单位池只是候选单位；真正进入战斗仍需先放置到地图，再开始战斗。

## 入口工具

统一使用 `manage_scene_units`，场景单位不再提供独立旧工具入口。

## 动作速查

- 创建友方：`action="spawn_ally"`，传 `profile_id`，可选 `name`、`unit_id`。常用模板包括 `fighter_companion`、`apprentice_wizard`、`acolyte_healer`。
- 创建怪物：`action="spawn_monsters"`，传 `monster_index` 和 `count`，可选 `faction`。`monster_index` 使用 Open5e 英文 slug，例如 `goblin`、`wolf`、`bugbear`。
- 清理死亡档案：`action="clear_dead_units"`，可传 `unit_ids` 部分清理；不传则清理全部死亡单位档案。
- 查看说明：`action="help"`。

## 使用原则

- 开局默认需要友方战士时，用 `profile_id="fighter_companion"`，并让 `unit_id` 保持为 `fighter_companion`；该模板是名为格林的可靠沉稳战士队友，不是剧情 NPC 修达。
- 生成怪物后，它们只进入 `scene_units`；如果要开战，必须再用 `manage_space` 把玩家、友方和敌人放到当前地图上。
- 开始战斗时，`start_combat.combatant_ids` 必须显式包含所有参战友方和敌方 ID；不要只列敌人。
- 生成多个同类怪物时，一次性传 `count`，不要连续多次调用生成同一种怪物。
- 结束战斗时，HP 为 0 的敌人会自动按 CR 发放战斗 XP；敌人投降、被俘、被驱散或用非致命方式被战胜但 HP 仍大于 0 时，必须在战斗收束计划里标为被战胜。单纯逃跑、撤退、传送离开的敌人只标为离场，不应计入战斗 XP。
- 死亡单位档案用于搜刮、辨认、剧情追踪。只有当这些环节结束，或明确不再需要尸体信息时，才清理档案。
