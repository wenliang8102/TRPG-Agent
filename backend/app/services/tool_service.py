"""Tool definitions and execution service."""

from __future__ import annotations

import random
from functools import lru_cache

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import Command, interrupt

from app.calculation.predefined_characters import PREDEFINED_CHARACTERS


@tool
def weather(city: str, unit: str = "c") -> dict:
    """Get simple weather info for a city.

    Args:
        city: Target city name.
        unit: Temperature unit, supports "c" or "f".
    """
    normalized_unit = (unit or "c").strip().lower()
    if normalized_unit not in {"c", "f"}:
        normalized_unit = "c"

    city_name = (city or "").strip() or "unknown"
    temperature_c = 22
    temperature = temperature_c if normalized_unit == "c" else int(temperature_c * 9 / 5 + 32)

    return {
        "city": city_name,
        "temperature": temperature,
        "unit": normalized_unit,
        "condition": "clear",
        "source": "mock",
    }


@tool
def request_dice_roll(reason: str, formula: str = "1d20") -> dict:
    """Request a dice roll from the player for resolving an action (e.g., kicking a door).
    
    Args:
        reason: The narrative reason for the roll (e.g., "破门力量检定").
        formula: The dice formula (e.g., "1d20").
    """
    # 挂起 Graph 并将请求下发前端呈现按钮
    user_response = interrupt({
        "type": "dice_roll",
        "reason": reason,
        "formula": formula,
    })
    
    # 恢复执行时：如果在后端自动生成随机数
    if user_response == "confirmed":
        parts = formula.lower().split('d')
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            count, sides = int(parts[0]), int(parts[1])
            total = sum(random.randint(1, sides) for _ in range(count))
        else:
            total = random.randint(1, 20) # fallback
        return {"roll_result": total, "status": "success", "note": f"Rolled {total}."}
    
    return {"status": "failed", "note": "Player rejected the roll or unknown action."}


@tool
def load_character_profile(
    role_class: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command | str:
    """Load a predefined character profile based on the role class (e.g., '战士', '法师', '游荡者').
    This handles loading all abilities, HP, and AC automatically into the game state.

    Args:
        role_class: The character class to load. Current supported values: "战士", "法师", "游荡者".
    """
    key = role_class.strip()
    if key not in PREDEFINED_CHARACTERS:
        return f"未找到对应职业 '{key}'。支持的预设职业为：{', '.join(PREDEFINED_CHARACTERS.keys())}。"

    profile = PREDEFINED_CHARACTERS[key]
    
    import json
    
    # 依赖 LangGraph 机制原地更新 PlayerState 节点的共享状态
    # 并且返回 ToolMessage 防止节点因为缺少工具执行确认而报错
    return Command(
        update={
            "player": profile,
            "messages": [
                ToolMessage(
                    content=f"已成功加载角色卡：{key}。\n属性如下：{json.dumps(profile, ensure_ascii=False, indent=2)}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


@lru_cache(maxsize=1)
def get_tools() -> list[BaseTool]:
    return [weather, request_dice_roll, load_character_profile]

