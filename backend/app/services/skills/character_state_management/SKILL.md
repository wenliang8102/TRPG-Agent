# 角色状态调整技能

当玩家或战斗单位的客观状态需要变化时使用本技能。先确认用户确实要求执行变化；如果只是询问规则或可能后果，不要调用执行类工具。

## 入口工具

统一使用 `modify_character_state`。不要调用旧的 `grant_xp`、`level_up`、`choose_arcane_tradition`、`apply_condition`、`remove_condition`；这些只保留给历史调用兼容。

## 动作速查

- 普通数值调整：`action="update"`，传 `target_id` 与 `changes`。
- 增加经验：`action="grant_xp"`，传 `payload={"amount": 经验值}`。
- 玩家升级：`action="level_up"`，不需要额外 payload。
- 法师学派：`action="choose_arcane_tradition"`，传 `payload={"tradition": "abjuration"}` 或 `{"tradition": "evocation"}`。
- 施加状态：`action="apply_condition"`，传 `payload={"target_id": 目标ID, "condition_id": 状态ID}`，可选 `source_id`、`duration`。
- 移除状态：`action="remove_condition"`，传 `payload={"target_id": 目标ID, "condition_id": 状态ID}`。

## 常用 `changes`

- 伤害或治疗：`{"hp_delta": -5}` 或 `{"hp_delta": 5}`。
- 直接设置 HP：`{"set_hp": 8}`。
- 调整 AC：`{"ac": 16}`。
- 调整速度：`{"speed": 20}`。
- 调整能力值：`{"abilities": {"str": 16}}`。
- 调整资源：`{"resource_delta": {"spell_slot_lv1": -1}}`。
- 恢复资源到上限：`{"set_resource": {"spell_slot_lv1": "max"}}`。

## 使用原则

- HP 变化优先用 `hp_delta`，让工具处理边界。
- 法术位消耗通常由 `cast_spell` 自动处理；只有长休、剧情奖励、特殊恢复时才用本技能手动调整资源。
- 战斗中的攻击、施法、回合推进仍使用战斗工具；本技能只负责无法由专门工具自动处理的状态变更。
- 如果需要知道目标 ID，先从 HUD 读取；HUD 不足时再用 `inspect_unit`。
