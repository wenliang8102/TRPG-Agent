"""雷鸣波 (Thunderwave) — 1环塑能，15尺立方 AoE + CON 豁免 + 推离效果"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Thunderwave",
    "name_cn": "雷鸣波",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "self (15-foot cube)",
    "description": "15尺立方区域内的每个生物必须进行体质豁免。失败受2d8点雷鸣伤害并被推离10尺，成功减半且不被推离。法术会发出300尺内可听见的巨响。升环每高一环伤害增加1d8。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """2d8 雷鸣伤害（升环每环+1d8），CON 豁免成功减半且不被推离，失败全额且推离10尺"""
    # 基础 1 环 2d8，每升一环加 1d8
    dice_count = 2 + max(0, slot_level - 1)
    formula = f"{dice_count}d8"

    # 生成伤害和DC
    dmg_roll = d20.roll(formula)
    full_damage = max(1, dmg_roll.total)
    half_damage = full_damage // 2
    
    spell_dc = get_spell_dc(caster)
    caster_name = caster.get("name", "未知施法者")

    lines = [
        f"{caster_name} 施放 雷鸣波（{slot_level}环）— DC {spell_dc} CON(体质) 豁免",
        f"伴随着方圆300尺内都能听见的雷霆巨响，一道汹涌的冲击波向外扫去！",
        f"伤害骰: {dmg_roll} = {full_damage} 雷鸣伤害"
    ]
    
    hp_changes = []
    
    for target in targets:
        target_name = target.get("name", "未知目标")
        con_mod = target.get("modifiers", {}).get("con", 0)
        
        # 体质豁免检定
        save_roll = d20.roll(f"1d20+{con_mod}")
        saved = save_roll.total >= spell_dc
        
        actual_damage = half_damage if saved else full_damage
        save_text = f"豁免成功({save_roll})" if saved else f"豁免失败({save_roll})"
        push_text = "未被推离" if saved else "被猛烈地推离了 10 尺！"
        
        lines.append(f"  → {target_name}: {save_text} — 受到 {actual_damage} 点雷鸣伤害，[{push_text}]")
        
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
