from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="guiding_bolt_mark",
    name_cn="闪光标记",
    description="目标被秘法微光照亮，针对该目标的攻击检定将获得优势。",
    effects=CombatEffects(
        defend_advantage="advantage",
        consume_on_attacked=True,
    ),
)
