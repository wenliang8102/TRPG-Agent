# 职业动作技能

当玩家描述主动使用职业能力时，优先使用 `use_class_action`。

适用表达：

- “我使用回气”
- “我使用动作如潮”
- “我选择摔绊攻击、精准攻击、恐吓攻击作为战技”
- “我命中后使用摔绊攻击”
- “我用恐吓攻击”
- “我用推撞攻击”
- “我鼓舞队友”

基础用法：

- 查询可用职业动作：`use_class_action(action_id="")`
- 回气：`use_class_action(action_id="second_wind")`
- 动作如潮：`use_class_action(action_id="action_surge")`
- 选择战技：`use_class_action(action_id="choose_maneuvers", payload={"maneuvers": ["trip_attack", "precision_attack", "menacing_attack"]})`
- 鼓舞：`use_class_action(action_id="rally", target_id="ally_1")`

战斗大师战技：

- 当前可选择：`trip_attack`、`precision_attack`、`menacing_attack`、`pushing_attack`、`riposte`、`rally`
- 选择战技只记录到角色状态，不消耗卓越骰。
- `trip_attack`、`menacing_attack`、`pushing_attack` 已接入攻击流程；攻击时可传 `maneuver_id`。
- `pushing_attack` 第一版记录 `forced_movement_pending`，由后续空间动作或 DM 裁定具体落点。

2014 PHB 规则口径：

- `second_wind` 是附赠动作，恢复 `1d10 + 战士等级`，短休或长休恢复。
- `action_surge` 在自己回合获得额外动作，短休或长休恢复。
- 战斗大师 3 级选择 3 个战技，拥有 4 枚 d8 卓越骰。
- 摔绊攻击必须在武器攻击命中后使用，消耗 1 枚卓越骰，追加卓越骰伤害；大型或更小目标进行力量豁免，失败倒地。
- 恐吓攻击必须在武器攻击命中后使用，消耗 1 枚卓越骰，追加卓越骰伤害；目标进行感知豁免，失败恐慌直到你的下一回合结束。
- 推撞攻击必须在武器攻击命中后使用，消耗 1 枚卓越骰，追加卓越骰伤害；大型或更小目标进行力量豁免，失败被推离至多 15 尺。
- 鼓舞使用附赠动作，消耗 1 枚卓越骰；目标获得 `卓越骰 + 魅力调整值` 的临时 HP。
