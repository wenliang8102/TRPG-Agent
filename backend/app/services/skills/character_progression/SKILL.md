# 角色成长与子职技能

当玩家角色获得经验、升级，或需要选择职业成长分支时使用本技能。成长会永久改变角色卡；如果玩家只是在询问规则或比较选项，不要执行状态写入。

## 入口工具

统一使用 `modify_character_state`。不要调用旧的 `grant_xp`、`level_up`、`choose_arcane_tradition`、`choose_fighter_archetype`；这些只保留给历史调用兼容。

如需重新查看本说明，调用 `modify_character_state(action="help", payload={"topic": "progression"})`。

## 动作速查

- 增加经验：`action="grant_xp"`，传 `payload={"amount": 经验值}`，可选 `reason` 或 `payload.reason`。
- 玩家升级：`action="level_up"`，不需要额外 payload。
- 法师奥术传承：`action="choose_arcane_tradition"`，传 `payload={"tradition": "abjuration"}` 或 `{"tradition": "evocation"}`。
- 战士武术范型：`action="choose_fighter_archetype"`，传 `payload={"archetype": "champion"}`、`{"archetype": "battle_master"}` 或 `{"archetype": "eldritch_knight"}`。

## 升级约束

- 只有 XP 达到下一级门槛时才执行 `action="level_up"`；经验不足时不要手动改 `level`。
- 当法师升到 2 级时，必须继续处理奥术传承选择。直到 `arcane_tradition` 写入前，不要把这次升级视为完整完成。
- 当战士升到 3 级时，必须继续处理武术范型选择。直到 `fighter_archetype` 写入前，不要把这次升级视为完整完成。
- 当前升级表只覆盖已实现的职业和等级。工具返回“不支持”时，以工具结果为准，不要自行补写未实现等级的收益。

## 子职选项

- 法师 `abjuration`：防护学派，写入 `arcane_tradition="abjuration"`，授予 `arcane_ward`。
- 法师 `evocation`：塑能学派，写入 `arcane_tradition="evocation"`，授予 `sculpt_spells`。
- 战士 `champion`：勇士，写入 `fighter_archetype="champion"`，授予 `improved_critical`。
- 战士 `battle_master`：战斗大师，写入 `fighter_archetype="battle_master"`，授予卓越战技相关资源。
- 战士 `eldritch_knight`：奥法骑士，写入 `fighter_archetype="eldritch_knight"`，授予施法与武器联结相关字段。

## 使用原则

- 成长流程只改玩家角色；怪物临时强化、治疗、资源恢复仍属于角色状态调整。
- 升级后的 HP、法术位、职业资源和职业特性由工具统一结算，不要用普通 `changes` 手动拼出同一结果。
- 子职选择一旦写入，不要重复覆盖；如果玩家想重选，应先明确这是一次角色重置或剧情许可的重训。
