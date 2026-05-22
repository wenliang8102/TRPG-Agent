"""专长轻量注册表；只负责查找和应用，不承担完整规则引擎职责。"""

from __future__ import annotations

from collections.abc import Callable

from app.services.feats.basic import apply_sharpshooter, apply_tough

FeatApply = Callable[[dict], list[str]]

FEATS: dict[str, dict[str, str | FeatApply]] = {
    "tough": {
        "name": "Tough",
        "apply": apply_tough,
    },
    "sharpshooter": {
        "name": "Sharpshooter",
        "apply": apply_sharpshooter,
    },
}


def apply_feat(target: dict, feat_id: str) -> tuple[str, list[str]]:
    """按注册表应用专长，未知专长直接抛错，避免静默写入错误字段。"""
    feat = FEATS[feat_id]
    apply = feat["apply"]
    return str(feat["name"]), apply(target)  # type: ignore[operator]


def available_feat_ids() -> list[str]:
    """为工具错误提示提供稳定的可选专长列表。"""
    return sorted(FEATS)
