"""奥术结界 (Arcane Ward) — 防护学派法师特性

受伤时结界优先吸收伤害；施放防护系法术时恢复。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="arcane_ward",
    name_cn="奥术结界",
    description="防护学派法师的护盾结界，受伤时优先吸收伤害。",
    effects=CombatEffects(),
)


def absorb_damage(condition: dict, target: dict, damage: int) -> tuple[int, list[str]]:
    """结界优先吸收伤害。返回 (剩余伤害, 日志行)。"""
    extra = condition.get("extra", {})
    ward_hp = extra.get("ward_hp", 0)
    if ward_hp <= 0:
        return damage, []

    name = target.get("name", "?")
    absorbed = min(ward_hp, damage)
    remaining = damage - absorbed
    extra["ward_hp"] = ward_hp - absorbed

    lines = [f"  [奥术结界] 吸收 {absorbed} 点伤害（结界 HP: {ward_hp} → {extra['ward_hp']}）"]
    if extra["ward_hp"] <= 0:
        lines.append(f"  {name} 的奥术结界已破碎！")

    return remaining, lines
