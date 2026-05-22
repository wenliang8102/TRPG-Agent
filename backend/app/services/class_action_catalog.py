"""职业动作目录。"""

from __future__ import annotations

from typing import Any


# 中文注释：动作可见性只依赖职业特性，放在轻量模块里避免上下文装配器牵动工具层导入。
REQUIRED_CLASS_ACTION_FEATURES: dict[str, tuple[str, ...]] = {
    "second_wind": ("second_wind",),
    "action_surge": ("action_surge",),
    "choose_maneuvers": ("combat_superiority",),
    "trip_attack": ("combat_superiority",),
    "rally": ("combat_superiority",),
    "arcane_recovery": ("arcane_recovery",),
}


def available_class_actions(actor: dict[str, Any]) -> list[str]:
    """按角色当前职业特性列出可主动使用的职业动作。"""
    features = set(actor.get("class_features", []))
    return [
        action_id
        for action_id, required in REQUIRED_CLASS_ACTION_FEATURES.items()
        if all(feature_id in features for feature_id in required)
    ]
