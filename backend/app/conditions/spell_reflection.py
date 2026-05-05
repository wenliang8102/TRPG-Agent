"""法术反射 — Spectator 对未命中的法术攻击使用反应改指目标。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="spell_reflection",
    name_cn="法术反射",
    description="当针对自身的法术攻击未命中时，使用反应把法术改指向可见目标。",
    effects=CombatEffects(),
)


def on_spell_attack_missed(condition: dict, actor: dict, context: dict) -> dict | None:
    """反射只声明决策结果，实际改判由法术攻击 resolver 执行。"""
    if not actor.get("reaction_available", False):
        return None
    candidates = [
        target
        for target in context.get("targets", [])
        if target.get("id") != actor.get("id") and target.get("hp", 0) > 0
    ]
    if not candidates:
        return None
    actor["reaction_available"] = False
    return {
        "target": candidates[0],
        "lines": [f"  [法术反射] {actor.get('name', '?')} 使用反应，将法术反射给 {candidates[0].get('name', '?')}。"],
    }
