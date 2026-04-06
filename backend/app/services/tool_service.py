"""Tool definitions and execution service."""

from __future__ import annotations

import random
from functools import lru_cache

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from app.calculation.predefined_characters import PREDEFINED_CHARACTERS


@tool
def weather(city: str, unit: str = "c") -> dict:
    """获取指定城市的天气信息。

    Args:
        city: 目标城市名称。
        unit: 温度单位，支持 "c" (摄氏度) 或 "f" (华氏度)。
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
def request_dice_roll(
    reason: str,
    state: Annotated[dict, InjectedState], 
    ability: Literal["str", "dex", "con", "int", "wis", "cha"] | None = None,
    formula: str = "1d20"
) -> dict:
    """向玩家发起掷骰请求以判断动作结果（例如：“破门力量检定”）。
    如果提供了 `ability` 参数，系统会自动获取对应角色的属性值，并计算修正附加到总分中。
    注意：你在接下来的叙事中绝对不需要（也不应该）手动二次加上修正值计算结果，因为本工具返回的 final_total 已经包含了修正值！
    
    Args:
        reason: 掷骰的叙事原因，例如 "破门力量检定"。
        ability: 【强烈推荐】动作所依赖的属性 ("str", "dex", "con", "int", "wis", "cha")。
        formula: 掷骰公式，默认为 "1d20"。
    """
    
    # 提取属性修正值：未加载角色/未传属性设为 0
    modifier = 0
    if ability and state.get("player") and "modifiers" in state["player"]:
        modifier = state["player"]["modifiers"].get(ability, 0)
        
    # 挂起 Graph 并将请求下发前端呈现按钮
    user_response = interrupt({
        "type": "dice_roll",
        "reason": reason,
        "ability": ability,
        "formula": formula,
    })
    
    # 恢复执行时：如果在后端自动生成随机数
    if user_response == "confirmed":
        parts = formula.lower().split('d')
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            count, sides = int(parts[0]), int(parts[1])
            raw_roll = sum(random.randint(1, sides) for _ in range(count))
        else:
            raw_roll = random.randint(1, 20) # 兜底
            
        final_total = raw_roll + modifier
        
        # 组装返回给大模型和未来前端的数据对象
        # 提供标准化 note_str 指导大模型输出结构，避免其“左右脑互搏”算错加法
        sign = '+' if modifier >= 0 else ''
        modifier_str = f"属性修正({ability}){sign}{modifier}" if ability else "无属性修正"
        
        note_str = (
            f"系统已完成严谨计算：基础骰值(raw_roll)={raw_roll}，"
            f"{modifier_str}，最终总值(final_total)={final_total}。\n"
            "【特别指令】：请向玩家如实播报这个算式（例：“基础X + 修正Y = 最终Z”），并严格仅使用 final_total 判断检定成败，不要自己重新做加法！"
        )

        return {
            "raw_roll": raw_roll,
            "modifier": modifier,
            "final_total": final_total,
            "status": "success",
            "note": note_str
        }
    
    return {"status": "failed", "note": "玩家拒绝了掷骰或动作未知。"}


@tool
def load_character_profile(
    role_class: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command | str:
    """根据给定的职业（如'战士'、'法师'、'游荡者'）读取并加载该角色的预设属性卡。
    此工具会自动把角色的血量(HP)、护甲(AC)和各项能力值/修正值写入游戏的主状态中。
    在需要与角色互动前使用此工具为玩家初始化。

    Args:
        role_class: 需要加载的角色职业名称。当前支持："战士", "法师", "游荡者"。
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

