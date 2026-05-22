"""人类定身术 (Hold Person) — 2环附魔，WIS 豁免否则麻痹。每回合结束重新豁免。专注。"""

from app.conditions._base import build_condition_extra, create_condition
from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Hold Person",
    "name_cn": "人类定身术",
    "level": 2,
    "school": "enchantment",
    "casting_time": "action",
    "range": "60 feet",
    "description": "目标进行 WIS 豁免，失败则被麻痹。每回合结束可重新进行 WIS 豁免。需要专注，持续 1 分钟。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """单目标 WIS 豁免，失败施加 paralyzed 条件（save_ends 每回合结束重豁免）"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    target = targets[0]
    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")
    spell_dc = get_spell_dc(caster)

    # 仅对类人生物有效 — 简化为 creature_type 检查
    creature_type = target.get("creature_type", "humanoid")
    if creature_type not in ("humanoid", ""):
        return {"lines": [
            f"{caster_name} 施放 人类定身术 → {target_name}",
            f"  → 但 {target_name} 不是类人生物（{creature_type}），法术无效！",
        ]}

    from app.services.tools._helpers import roll_actor_save

    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "wis")
    lines = [f"{caster_name} 施放 人类定身术 → {target_name} — DC {spell_dc} WIS 豁免"]

    if auto_fail_reason:
        lines.append(f"  → 豁免自动失败（{auto_fail_reason}）！{target_name} 陷入麻痹！")
    elif save_roll.total >= spell_dc:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免成功({roll_text})！{target_name} 抵抗了法术")
        return {"lines": lines}
    else:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免失败({roll_text})！{target_name} 陷入麻痹！")

    conditions = target.setdefault("conditions", [])
    conditions.append(
        create_condition(
            "paralyzed",
            source_id="concentration:hold_person",
            duration=10,
            extra=build_condition_extra(save_ends={"ability": "wis", "dc": spell_dc}),
        )
    )

    return {"lines": lines}
