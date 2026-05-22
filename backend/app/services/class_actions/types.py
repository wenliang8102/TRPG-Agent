"""职业动作框架的通用类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ClassActionContext:
    """职业动作需要同时看角色、目标和当前战斗状态。"""

    actor: dict[str, Any]
    target: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


@dataclass(slots=True)
class ClassActionResult:
    """职业动作执行后统一返回文本和状态增量。"""

    lines: list[str]
    update: dict[str, Any]
