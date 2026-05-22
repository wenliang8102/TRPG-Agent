"""亡者丧钟 (Toll the Dead) — 戏法/死灵，WIS 豁免，黯蚀伤害（残血增强）"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Toll the Dead",
    "name_cn": "亡者丧钟",
    "level": 0,
    "school": "necromancy",
    "casting_time": "action",
    "range": "60 feet",
    "description": "目标进行 WIS 豁免，失败受到 1d8 黯蚀伤害。若目标已损失生命值则改为 1d12。伤害随等级增长。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, *, cantrip_scale: int = 1, **_) -> SpellResult:
    """单目标 WIS 豁免，1d8（残血 1d12）黯蚀伤害"""
    from app.services.tools._helpers import apply_damage_to_target, roll_actor_save

    if not targets:
        return {"lines": ["未指定目标。"]}

    target = targets[0]
    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")
    spell_dc = get_spell_dc(caster)

    # 残血时增强骰子
    is_damaged = target.get("hp", 0) < target.get("max_hp", 1)
    die = "d12" if is_damaged else "d8"
    damage_formula = f"{cantrip_scale}{die}"

    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "wis")
    saved = False if auto_fail_reason else save_roll.total >= spell_dc

    lines = [f"{caster_name} 施放 亡者丧钟 — DC {spell_dc} WIS 豁免"]
    hp_changes: list[dict] = []

    if saved:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → {target_name}: 豁免成功({roll_text}) — 未受伤害")
    else:
        dmg_roll = d20.roll(damage_formula)
        damage = max(1, dmg_roll.total)
        if auto_fail_reason:
            roll_text = f"自动失败（{auto_fail_reason}）"
        else:
            roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → {target_name}: 豁免失败({roll_text}) — {dmg_roll} = {damage} 黯蚀伤害")
        _, hc, damage_lines = apply_damage_to_target(target, damage, damage_type="necrotic")
        hp_changes.append(hc)
        lines.extend(f"  {line}" for line in damage_lines)

    return {"lines": lines, "hp_changes": hp_changes}
