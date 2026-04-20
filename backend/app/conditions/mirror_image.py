"""镜影术条件 — 追踪镜像数量，被攻击时 d20 判定是否偏转到镜像"""

import d20

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="mirror_image",
    name_cn="镜影术",
    description="存在 1-3 个幻影镜像。被攻击时有概率偏转攻击到镜像。",
    effects=CombatEffects(),
)


def on_attack_resolved(condition: dict, attacker: dict, target: dict, roll_info: dict) -> dict | None:
    """在目标已被命中后，改判是否被镜像偏转。"""
    extra = condition.get("extra", {})
    images = extra.get("images", 0)
    if images <= 0 or not roll_info.get("hit"):
        return None

    threshold = {3: 6, 2: 8, 1: 11}.get(images, 11)
    roll = d20.roll("1d20")
    target_name = target.get("name", "?")
    hit_total = roll_info.get("hit_total", 0)

    lines: list[str] = []

    if roll.total < threshold:
        lines.append(f"  [镜影术] {roll} < {threshold}，攻击穿过幻影直奔本体！（剩余 {images} 镜像）")
        return {"lines": lines}

    image_ac = 10 + target.get("modifiers", {}).get("dex", 0)
    if hit_total >= image_ac:
        extra["images"] = images - 1
        remaining = extra["images"]
        lines.append(f"  [镜影术] {roll} >= {threshold}，攻击转向镜像！命中镜像（AC {image_ac}），镜像破碎！（剩余 {remaining} 镜像）")
        if remaining <= 0:
            condition["duration"] = 0
            lines.append(f"  {target_name} 的所有镜像已消散！")
    else:
        lines.append(f"  [镜影术] {roll} >= {threshold}，攻击转向镜像但未命中（AC {image_ac}）！（剩余 {images} 镜像）")

    return {
        "hit": False,
        "crit": False,
        "deflected": True,
        "lines": lines,
        "stop_processing": True,
    }
