"""法师护甲 (Mage Armor) - 1环防护派系"""

from app.conditions._base import ActiveCondition, has_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Mage Armor",
    "name_cn": "法师护甲",
    "level": 1,
    "school": "abjuration",
    "casting_time": "action",
    "range": "touch",
    "description": "触摸一个未穿戴护甲的自愿生物，直到法术结束前，目标的 AC 至少为 13 + 敏捷修正。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **kwargs) -> SpellResult:
    """应用法师护甲。

    当前项目尚未实现护甲装备系统，因此这里直接把 AC 提升到
    13 + 敏捷修正的下限，并通过 condition 记录法术来源与效果。"""
    target = targets[0]
    target_name = target.get("name", "?")

    old_ac = int(target.get("ac", 10))
    dex_mod = int(target.get("modifiers", {}).get("dex", 0))
    mage_armor_ac = 13 + dex_mod
    target["ac"] = max(old_ac, mage_armor_ac)

    lines = [
        f"{caster.get('name', '?')} 施放了法师护甲。",
        f"{target_name} 的 AC: {old_ac} → {target['ac']}。",
    ]

    # 用状态系统记录该增益，供查询/后续扩展使用。
    conditions = target.setdefault("conditions", [])
    if not has_condition(conditions, "mage_armor"):
        conditions.append(
            ActiveCondition(
                id="mage_armor",
                source_id=caster.get("name", "Mage Armor"),
                duration=None,
                extra={"ac_floor": mage_armor_ac},
            ).model_dump()
        )
        lines.append(f"{target_name} 获得了【法师护甲】状态。")
    else:
        lines.append(f"{target_name} 身上的【法师护甲】被刷新。")

    return {"lines": lines}
