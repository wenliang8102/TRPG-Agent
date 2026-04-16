"""火球术 (Fireball) — 3环塑能，150尺/半径20尺 AoE + DEX 豁免"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Fireball",
    "name_cn": "火球术",
    "level": 3,
    "school": "evocation",
    "casting_time": "action",
    "range": "150 feet (20-foot radius sphere)",
    "description": "目标点周围半径20尺球状区域内的每个生物必须进行敏捷豁免。失败受8d6点火焰伤害，成功减半。升环每高一环伤害增加1d6。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """8d6 火焰伤害（3环以上升环每环+1d6），DEX 豁免成功减半"""
    # 基础 3 环 8d6，每升一环加 1d6
    dice_count = 8 + max(0, slot_level - 3)
    formula = f"{dice_count}d6"

    # 生成伤害和DC
    dmg_roll = d20.roll(formula)
    full_damage = max(1, dmg_roll.total)
    half_damage = full_damage // 2
    
    spell_dc = get_spell_dc(caster)
    caster_name = caster.get("name", "未知施法者")

    lines = [
        f"{caster_name} 施放 火球术（{slot_level}环）— DC {spell_dc} DEX 豁免",
        f"伤害骰: {dmg_roll} = {full_damage} 火焰伤害"
    ]
    
    hp_changes = []
    
    for target in targets:
        target_name = target.get("name", "未知目标")
        dex_mod = target.get("modifiers", {}).get("dex", 0)
        
        # 敏捷豁免检定
        save_roll = d20.roll(f"1d20+{dex_mod}")
        saved = save_roll.total >= spell_dc
        
        actual_damage = half_damage if saved else full_damage
        save_text = f"豁免成功({save_roll})" if saved else f"豁免失败({save_roll})"
        
        lines.append(f"  → {target_name}: {save_text} — 受到 {actual_damage} 点火焰伤害")
        
        # 执行 HP 扣除
        old_hp = target.get("hp", 0)
        new_hp = max(0, old_hp - actual_damage)
        target["hp"] = new_hp
        
        hp_changes.append({
            "id": target.get("id", ""),
            "name": target_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": target.get("max_hp", old_hp)
        })
        
        lines.append(f"  {target_name} HP: {old_hp} → {new_hp}")
        if new_hp == 0:
            lines.append(f"  {target_name} 倒下了!")

    return {
        "lines": lines,
        "hp_changes": hp_changes
    }
