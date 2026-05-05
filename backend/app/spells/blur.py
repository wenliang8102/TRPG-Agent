"""模糊术 (Blur) — 2环幻术，自身防御型专注法术。"""

from app.conditions._base import create_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Blur",
    "name_cn": "模糊术",
    "level": 2,
    "school": "illusion",
    "casting_time": "action",
    "range": "self",
    "description": "你的身形变得模糊，攻击者对你发动攻击检定具有劣势。需要专注，持续 1 分钟。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """自身挂载 blurred 条件，效果由攻击优势系统读取。"""
    target = targets[0]
    caster_name = caster.get("name", "?")
    target["conditions"] = [c for c in target.get("conditions", []) if c.get("id") != "blurred"]
    target.setdefault("conditions", []).append(create_condition("blurred", source_id="concentration:blur", duration=10))
    return {"lines": [f"{caster_name} 施放 模糊术，身影开始扭曲晃动。"]}
