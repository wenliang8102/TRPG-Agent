from app.conditions._base import ConditionDef

CONDITION_DEF = ConditionDef(
    id="mage_armor",
    name_cn="法师护甲",
    description="目标被奥术护甲包裹，在当前项目中其 AC 至少视为 13 + 敏捷修正。",
)


def modify_ac(unit: dict, current_ac: int) -> int:
    """法师护甲 AC 下限：13 + DEX 修正，与当前 AC 取大"""
    dex_mod = unit.get("modifiers", {}).get("dex", 0)
    return max(current_ac, 13 + dex_mod)
