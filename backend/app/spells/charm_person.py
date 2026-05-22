"""魅惑人类 (Charm Person) — 1环附魔，WIS 豁免失败后被魅惑。"""

from app.conditions._base import create_condition
from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Charm Person",
    "name_cn": "魅惑人类",
    "level": 1,
    "school": "enchantment",
    "casting_time": "action",
    "range": "30 feet",
    "description": "一个类人生物进行 WIS 豁免，失败则被你魅惑。若正在与你或盟友战斗，目标对豁免具有优势；本地实现保留普通豁免。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """单目标 WIS 豁免，失败挂载 charmed 条件。"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    from app.services.tools._helpers import roll_actor_save

    target = targets[0]
    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")
    spell_dc = get_spell_dc(caster)
    lines = [f"{caster_name} 施放 魅惑人类 → {target_name} — DC {spell_dc} WIS 豁免"]

    if target.get("creature_type", "humanoid") not in ("humanoid", ""):
        lines.append(f"  → 但 {target_name} 不是类人生物，法术无效。")
        return {"lines": lines}

    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "wis")
    if auto_fail_reason:
        lines.append(f"  → 豁免自动失败（{auto_fail_reason}）！{target_name} 被魅惑。")
    elif save_roll.total >= spell_dc:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免成功({roll_text})，{target_name} 抵抗了法术。")
        return {"lines": lines}
    else:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免失败({roll_text})！{target_name} 被魅惑。")

    target.setdefault("conditions", []).append(create_condition("charmed", source_id=caster.get("id", ""), duration=60))
    return {"lines": lines}
