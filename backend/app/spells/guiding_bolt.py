"""曳光弹 (Guiding Bolt) — 1环塑能，远程法术攻击，光耀伤害及单次造优"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spellcasting_mod

SPELL_DEF: SpellDef = {
    "name": "Guiding Bolt",
    "name_cn": "曳光弹",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "120 feet",
    "description": "进行一次远程法术攻击。命中受到 4d6 点光耀伤害，并在你的下个回合结束前，对目标发动的下一次攻击检定具有优势。升环每高一环伤害增加 1d6。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """1个目标，远程法术攻击，4d6光耀伤害（升环每环+1d6），命中造优"""
    if not targets:
        return {"lines": ["未指定目标。"]}
        
    target = targets[0]  # 单体目标
    target_name = target.get("name", "未知目标")

    caster_level = caster.get("level", 1)
    prof_bonus = (caster_level - 1) // 4 + 2
    attack_bonus = prof_bonus + get_spellcasting_mod(caster)
    
    caster_name = caster.get("name", "未知施法者")
    target_ac = target.get("ac", 10)
    
    # 攻击检定 (d20 + 熟练加值 + 施法属性修正)
    attack_roll = d20.roll(f"1d20+{attack_bonus}")
    is_hit = attack_roll.total >= target_ac
    
    lines = [
        f"{caster_name} 施放 曳光弹（{slot_level}环） 发起远程法术攻击！"
    ]
    
    hp_changes = []
    
    if is_hit:
        # 计算伤害: 基础 1 环 4d6，每升一环加 1d6
        dice_count = 4 + max(0, slot_level - 1)
        formula = f"{dice_count}d6"
        dmg_roll = d20.roll(formula)
        actual_damage = max(1, dmg_roll.total)
        
        lines.append(f"  → 攻击检定: {attack_roll} >= AC {target_ac} (命中！)")
        lines.append(f"  伤害骰: {dmg_roll} = {actual_damage} 光耀伤害")
        lines.append(f"  [造优效果] 秘法的微光在 {target_name} 身上闪耀，对它发动的下一次攻击检定具有优势！(持续至 {caster_name} 下回合结束)")
        
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
    else:
        lines.append(f"  → 攻击检定: {attack_roll} < AC {target_ac} (未命中)")

    return {
        "lines": lines,
        "hp_changes": hp_changes
    }
