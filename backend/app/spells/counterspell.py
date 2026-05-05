"""法术反制 (Counterspell) — 3环防护，反应打断正在施放的法术"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spellcasting_mod

SPELL_DEF: SpellDef = {
    "name": "Counterspell",
    "name_cn": "法术反制",
    "level": 3,
    "school": "abjuration",
    "casting_time": "reaction",
    "reaction_trigger": "on_enemy_cast",
    "range": "60 feet",
    "description": "当60尺内一个生物正在施法时，以反应尝试打断。若目标法术环阶不高于本法术位则自动成功；否则进行施法属性检定，DC为10+目标法术环阶。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **context) -> SpellResult:
    """按法术反制规则判定触发中的法术是否失效。"""
    target_spell_level = context["trigger_spell_level"]
    target_spell_name = context.get("trigger_spell_name_cn", "目标法术")
    caster_name = caster.get("name", "?")
    target_name = targets[0].get("name", "?") if targets else context.get("trigger_caster_name", "?")

    lines = [f"{caster_name} 对 {target_name} 的 {target_spell_name} 施放 法术反制（{slot_level}环）。"]
    if target_spell_level <= slot_level:
        lines.append(f"{target_spell_name} 环阶不高于反制法术位，法术被打断。")
        return {"lines": lines, "blocked_action": True}

    dc = 10 + target_spell_level
    check = d20.roll(f"1d20{get_spellcasting_mod(caster):+d}")
    if check.total >= dc:
        lines.append(f"施法属性检定 DC {dc}: {check}，成功，{target_spell_name} 被打断。")
        return {"lines": lines, "blocked_action": True}

    lines.append(f"施法属性检定 DC {dc}: {check}，失败，{target_spell_name} 继续生效。")
    return {"lines": lines, "blocked_action": False}
