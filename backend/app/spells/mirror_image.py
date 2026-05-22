"""镜影术 (Mirror Image) — 2环幻术，创建 3 个幻影镜像分担攻击"""

from app.conditions._base import build_condition_extra, create_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Mirror Image",
    "name_cn": "镜影术",
    "level": 2,
    "school": "illusion",
    "casting_time": "action",
    "range": "self",
    "description": "创建 3 个幻影镜像。被攻击时 d20 判定攻击是否命中镜像。持续 1 分钟。非专注。",
    "concentration": False,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """自身施放，挂载 mirror_image 条件（10 回合持续，extra 追踪镜像数）"""
    target = targets[0]
    caster_name = caster.get("name", "?")

    conditions = target.setdefault("conditions", [])
    # 移除旧镜影
    target["conditions"] = [c for c in conditions if c.get("id") != "mirror_image"]
    conditions = target["conditions"]

    conditions.append(
        create_condition(
            "mirror_image",
            source_id=caster_name,
            duration=10,
            extra=build_condition_extra(images=3),
        )
    )

    return {"lines": [
        f"{caster_name} 施放 镜影术！",
        f"三个幻影镜像环绕在 {caster_name} 身边（持续 10 回合）",
    ]}
