"""通用法术解算器 — 提取 AoE 豁免 / 法术攻击的共性流程，
支持 class_features 钩子（如塑能塑法、死灵收割）。"""

from __future__ import annotations

import d20

from app.spells._base import SpellResult, get_spell_dc, get_spellcasting_mod


def _lazy_helpers():
    """延迟导入 _helpers 避免 spells → tools → spell_tools → spells 循环引用"""
    from app.services.tools._helpers import (
        apply_damage_to_target,
        apply_hp_change,
        compute_ac,
        remove_consume_on_attacked_conditions,
        roll_actor_save,
    )
    return apply_damage_to_target, apply_hp_change, compute_ac, remove_consume_on_attacked_conditions, roll_actor_save

# 回调类型提示
from typing import Callable
_OnHitCallback = Callable[[dict, dict, list[str]], None]


# ── AoE 豁免类法术解算 ─────────────────────────────────────────


def resolve_aoe_save(
    caster: dict,
    targets: list[dict],
    *,
    spell_name_cn: str,
    slot_level: int,
    damage_formula: str,
    damage_type: str,
    save_ability: str,
    spell_school: str = "",
    extra_per_target: str | None = None,
) -> SpellResult:
    """通用 AoE + 豁免 → 全额/减半 伤害解算。

    支持 class_features:
    - sculpt_spells（塑能学派）：塑能法术中友方自动豁免成功
    - grim_harvest（死灵学派）：非戏法法术击杀时施法者回血
    """
    spell_dc = get_spell_dc(caster)
    caster_name = caster.get("name", "?")
    caster_features = caster.get("class_features", [])
    save_label = _ability_label(save_ability)
    apply_damage_to_target, apply_hp_change, _, _, roll_actor_save = _lazy_helpers()

    dmg_roll = d20.roll(damage_formula)
    full_damage = max(1, dmg_roll.total)
    half_damage = full_damage // 2

    lines = [
        f"{caster_name} 施放 {spell_name_cn}（{slot_level}环）— DC {spell_dc} {save_label} 豁免",
        f"伤害骰: {dmg_roll} = {full_damage} {damage_type}伤害",
    ]
    hp_changes: list[dict] = []
    total_kill_damage = 0

    for target in targets:
        target_name = target.get("name", "?")
        target_side = target.get("side", "enemy")

        # 塑能塑法：塑能学派法术中，施法者指定的友方自动豁免成功
        sculpt = (
            "sculpt_spells" in caster_features
            and spell_school == "evocation"
            and target_side in ("player", "ally")
        )

        if sculpt:
            actual_damage = 0
            lines.append(f"  → {target_name}: [塑能塑法] 自动豁免，免受伤害！")
        else:
            save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, save_ability)
            if auto_fail_reason:
                saved = False
                roll_text = f"自动失败（{auto_fail_reason}）"
            else:
                saved = save_roll.total >= spell_dc
                roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
            actual_damage = half_damage if saved else full_damage
            save_text = f"豁免成功({roll_text})" if saved else f"豁免失败({roll_text})"
            lines.append(f"  → {target_name}: {save_text} — {actual_damage} {damage_type}伤害")

        if actual_damage > 0:
            dealt_damage, hc, damage_lines = apply_damage_to_target(target, actual_damage)
            hp_changes.append(hc)
            lines.extend(f"  {line}" for line in damage_lines)
            if hc["new_hp"] == 0:
                total_kill_damage += dealt_damage

        # 附加每目标效果描述（如雷鸣波的推离）
        if extra_per_target and actual_damage > 0:
            lines.append(f"  {extra_per_target}")

    # 死灵收割：非戏法法术击杀至少一个目标时，施法者回复 HP
    if "grim_harvest" in caster_features and total_kill_damage > 0:
        heal = slot_level * 2 if spell_school == "necromancy" else slot_level
        heal_hc = apply_hp_change(caster, heal)
        hp_changes.append(heal_hc)
        lines.append(f"  [死灵收割] {caster_name} 从死亡中汲取生命力，回复 {heal} HP!")

    return {"lines": lines, "hp_changes": hp_changes}


# ── 远程法术攻击类解算 ──────────────────────────────────────────


def resolve_spell_attack(
    caster: dict,
    target: dict,
    *,
    spell_name_cn: str,
    slot_level: int,
    damage_formula: str,
    damage_type: str,
    on_hit_extra: _OnHitCallback | None = None,
) -> SpellResult:
    """通用单目标法术攻击解算。

    on_hit_extra: 命中后的额外效果回调 (caster, target, lines) -> None
    """
    from app.services.tools._helpers import _determine_advantage_from_conditions
    apply_damage_to_target, _, compute_ac, remove_consume_on_attacked_conditions, _ = _lazy_helpers()

    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")
    caster_level = caster.get("level", 1)
    prof_bonus = (caster_level - 1) // 4 + 2
    attack_bonus = prof_bonus + get_spellcasting_mod(caster)

    target_ac = compute_ac(target)

    advantage = _determine_advantage_from_conditions(
        caster.get("conditions", []), target.get("conditions", [])
    )

    if advantage == "advantage":
        hit_expr = f"2d20kh1+{attack_bonus}"
    elif advantage == "disadvantage":
        hit_expr = f"2d20kl1+{attack_bonus}"
    else:
        hit_expr = f"1d20+{attack_bonus}"

    attack_roll = d20.roll(hit_expr)
    is_hit = attack_roll.total >= target_ac

    remove_consume_on_attacked_conditions(target)

    lines = [f"{caster_name} 施放 {spell_name_cn}（{slot_level}环） 发起远程法术攻击！"]
    hp_changes: list[dict] = []

    if is_hit:
        dmg_roll = d20.roll(damage_formula)
        actual_damage = max(1, dmg_roll.total)

        lines.append(f"  → 攻击检定: {attack_roll} >= AC {target_ac} (命中！)")
        lines.append(f"  伤害骰: {dmg_roll} = {actual_damage} {damage_type}伤害")

        _, hc, damage_lines = apply_damage_to_target(target, actual_damage)
        hp_changes.append(hc)
        lines.extend(f"  {line}" for line in damage_lines)

        if on_hit_extra:
            on_hit_extra(caster, target, lines)
    else:
        lines.append(f"  → 攻击检定: {attack_roll} < AC {target_ac} (未命中)")

    return {"lines": lines, "hp_changes": hp_changes}


# ── 内部工具 ────────────────────────────────────────────────────

_ABILITY_LABELS = {
    "str": "STR(力量)", "dex": "DEX(敏捷)", "con": "CON(体质)",
    "int": "INT(智力)", "wis": "WIS(感知)", "cha": "CHA(魅力)",
}

def _ability_label(ability: str) -> str:
    return _ABILITY_LABELS.get(ability, ability.upper())
