"""基础专长定义；第一版只落地可安全写入角色卡的字段。"""

from __future__ import annotations


def apply_tough(target: dict) -> list[str]:
    """健壮专长只影响最大生命值，规则效果足够简单，适合第一版直接写入。"""
    level = int(target.get("level", 1) or 1)
    bonus = 2 * level
    old_bonus = int(target.get("feat_tough_hp_bonus", 0) or 0)
    delta = bonus - old_bonus
    target["feat_tough_hp_bonus"] = bonus
    if delta > 0:
        target["max_hp"] = int(target.get("max_hp", 0) or 0) + delta
        target["hp"] = int(target.get("hp", 0) or 0) + delta
    return [f"  Tough: 最大 HP +{bonus}"]


def apply_sharpshooter(target: dict) -> list[str]:
    """神射手包含攻击选项，第一版先记录，后续再接入攻击流程。"""
    target["sharpshooter_enabled"] = False
    return ["  Sharpshooter: 已记录，攻击选项效果尚未自动结算"]
