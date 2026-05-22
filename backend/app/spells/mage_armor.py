"""法师护甲 (Mage Armor) - 1环防护派系

AC 提升效果由 conditions/mage_armor.py 的 modify_ac 钩子在 compute_ac() 时动态计算，
本法术只负责在目标身上挂载 mage_armor 条件。"""

from app.conditions._base import create_condition, has_condition
from app.services.tools._helpers import sync_ac_state
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
    """挂载 mage_armor 条件，AC 提升由 compute_ac() 动态处理"""
    target = targets[0]
    target_name = target.get("name", "?")
    caster_name = caster.get("name", "?")

    lines = [f"{caster_name} 施放了法师护甲。"]

    conditions = target.setdefault("conditions", [])
    if not has_condition(conditions, "mage_armor"):
        conditions.append(create_condition("mage_armor", source_id=caster_name))
        lines.append(f"{target_name} 获得了【法师护甲】状态（AC 至少为 13 + DEX 修正）。")
    else:
        lines.append(f"{target_name} 身上的【法师护甲】被刷新。")

    sync_ac_state(target)

    return {"lines": lines}
