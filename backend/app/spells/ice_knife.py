"""冰刃 (Ice Knife) — 1环咒法，远程攻击 + 爆炸 AoE 冷害"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spell_dc, get_spellcasting_mod

SPELL_DEF: SpellDef = {
    "name": "Ice Knife",
    "name_cn": "冰刃",
    "level": 1,
    "school": "conjuration",
    "casting_time": "action",
    "range": "60 feet",
    "description": "对首个目标进行远程法术攻击，命中受1d10穿刺伤害。随后冰刃爆炸，所有目标（首个及周围）需进行DEX豁免，失败受2d6冷冻伤害，成功减半。升环冷冻+1d6。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """结合远程攻击与范围豁免"""
    if not targets:
        return {"lines": ["没有指定任何目标！"], "hp_changes": []}

    caster_name = caster.get("name", "?")
    level = caster.get("level", 1)
    prof = (level - 1) // 4 + 2
    spell_mod = get_spellcasting_mod(caster)
    atk_bonus = prof + spell_mod
    spell_dc = get_spell_dc(caster)

    lines: list[str] = [f"{caster_name} 施放 冰刃（{slot_level}环）!"]
    hp_changes: list[dict] = []
    
    # 用于记录每个单位最终受到的总伤害
    damage_by_target = {t.get("id", f"target_{i}"): 0 for i, t in enumerate(targets)}

    # 1. 穿刺攻击首个目标
    primary = targets[0]
    p_id = primary.get("id", "target_0")
    p_name = primary.get("name", "?")

    from app.services.tools._helpers import compute_ac
    p_ac = compute_ac(primary)
    
    atk_roll = d20.roll(f"1d20+{atk_bonus}")
    hit = atk_roll.total >= p_ac
    
    lines.append(f"  → 远程法术攻击 {p_name}: {atk_roll} vs AC {p_ac}")
    if hit:
        pierce_roll = d20.roll("1d10")
        pierce_dmg = max(1, pierce_roll.total)
        lines.append(f"    命中！造成 {pierce_dmg} 穿刺伤害 ({pierce_roll})")
        damage_by_target[p_id] += pierce_dmg
    else:
        lines.append("    未命中。但冰刃依然在目标处破碎！")

    # 2. 爆炸冷冻伤害 (包括首个目标在内的所有目标)
    cold_dice = 2 + (slot_level - 1)
    cold_roll = d20.roll(f"{cold_dice}d6")
    full_cold = max(1, cold_roll.total)
    half_cold = full_cold // 2
    
    lines.append(f"\n  冰刃爆炸溅射！波及目标需进行 DC {spell_dc} DEX 豁免：")
    lines.append(f"  冷冻伤害骰: {cold_roll} = {full_cold} / {half_cold}(减半)")
    
    for i, target in enumerate(targets):
        t_id = target.get("id", f"target_{i}")
        t_name = target.get("name", "?")
        dex_mod = target.get("modifiers", {}).get("dex", 0)
        
        save_roll = d20.roll(f"1d20+{dex_mod}")
        saved = save_roll.total >= spell_dc
        
        actual_cold = half_cold if saved else full_cold
        save_text = f"成功({save_roll})" if saved else f"失败({save_roll})"
        
        lines.append(f"    → {t_name}: 豁免{save_text} — 受 {actual_cold} 冷冻伤害")
        damage_by_target[t_id] += actual_cold
        
    # 3. 统一结算 HP
    lines.append("\n  伤害结算：")
    for i, target in enumerate(targets):
        t_id = target.get("id", f"target_{i}")
        t_name = target.get("name", "?")
        total_dmg = damage_by_target[t_id]
        
        if total_dmg == 0:
            continue
            
        old_hp = target.get("hp", 0)
        new_hp = max(0, old_hp - total_dmg)
        target["hp"] = new_hp
        
        hp_changes.append({
            "id": target.get("id", ""),
            "name": t_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": target.get("max_hp", old_hp),
        })
        lines.append(f"    {t_name} HP: {old_hp} → {new_hp} (特计 -{total_dmg})")
        if new_hp == 0 and old_hp > 0:
            lines.append(f"    {t_name} 倒下了!")
            
    return {"lines": lines, "hp_changes": hp_changes}
