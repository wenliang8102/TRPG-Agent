"""职业动作注册表。"""

from __future__ import annotations

from collections.abc import Callable

from app.services.class_action_catalog import REQUIRED_CLASS_ACTION_FEATURES
from app.services.class_actions.types import ClassActionContext, ClassActionResult

ClassActionHandler = Callable[[ClassActionContext], ClassActionResult]

_REGISTRY: dict[str, ClassActionHandler] = {}
_REQUIRED_FEATURES: dict[str, tuple[str, ...]] = dict(REQUIRED_CLASS_ACTION_FEATURES)


def register_class_action(
    action_id: str,
    handler: ClassActionHandler,
    *,
    required_features: tuple[str, ...] = (),
) -> ClassActionHandler:
    """注册一个主动职业动作，并声明角色必须拥有的职业特性。"""
    _REGISTRY[action_id] = handler
    _REQUIRED_FEATURES[action_id] = required_features
    return handler


def run_class_action(action_id: str, context: ClassActionContext) -> ClassActionResult:
    """执行已注册的职业动作。"""
    return _REGISTRY[action_id](context)


def available_class_actions(actor: dict) -> list[str]:
    """按角色当前职业特性列出可主动使用的职业动作。"""
    features = set(actor.get("class_features", []))
    return [
        action_id
        for action_id, required in _REQUIRED_FEATURES.items()
        if all(feature_id in features for feature_id in required)
    ]


def has_class_action(action_id: str) -> bool:
    """查询动作是否已注册，供工具层做明确错误提示。"""
    return action_id in _REGISTRY
