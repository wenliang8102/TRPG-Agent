"""掷骰工具"""

from __future__ import annotations

from typing import Annotated, Literal

import d20
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState


@tool
def request_dice_roll(
    reason: str,
    state: Annotated[dict, InjectedState],
    ability: Literal["str", "dex", "con", "int", "wis", "cha"] | None = None,
    formula: str = "1d20",
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
    surprise: bool = False,
) -> dict:
    """向玩家发起掷骰请求以判断动作结果（例如："破门力量检定"）。
    如果提供了 `ability` 参数，系统会自动获取对应角色的属性值，并计算修正附加到总分中。
    先攻、潜行、察觉等常规 d20 检定可用 advantage 表达优势/劣势；若本次检定来自新版突袭导致的先攻劣势，可传 surprise=true 作为提醒。
    遭遇开战前的突袭/被突袭判定应按阵营或小队整体做一次检定，不要为每个玩家、友方或怪物逐个调用本工具；确定哪些单位被突袭后，把这些单位 ID 交给 start_combat 的 surprised_ids。
    注意：你在接下来的叙事中绝对不需要（也不应该）手动二次加上修正值计算结果，因为本工具返回的 final_total 已经包含了修正值！
    参数示例：{"reason": "搜索地精踪迹的感知检定", "ability": "wis", "formula": "1d20"}。

    Args:
        reason: 掷骰的叙事原因，例如 "破门力量检定"；群体突袭判定写成 "玩家小队是否察觉伏击" 这类整体原因。
        ability: 动作所依赖的属性，只能是 "str"、"dex"、"con"、"int"、"wis"、"cha"；不涉及属性时可不传。
        formula: 掷骰公式，默认为 "1d20"；属性修正由工具自动追加，不要写成 "1d20+3"。
        advantage: 常规 d20 检定的优势状态；新版突袭影响先攻时使用 "disadvantage"。
        surprise: 本次检定是否与突袭先攻有关；只作语义标记，不会额外修改骰子。突袭群体判定只调用一次。
    """
    modifier = 0
    if ability and state.get("player") and "modifiers" in state["player"]:
        modifier = state["player"]["modifiers"].get(ability, 0)

    roll_formula = _apply_advantage_formula(formula, advantage)
    result = d20.roll(roll_formula)
    raw_roll = result.total
    final_total = raw_roll + modifier

    sign = '+' if modifier >= 0 else ''
    modifier_str = f"属性修正({ability}){sign}{modifier}" if ability else "无属性修正"
    advantage_str = {"advantage": "优势", "disadvantage": "劣势"}.get(advantage, "常规")
    surprise_str = "；本次检定与新版突袭先攻有关；若这是开战前突袭判定，应代表整体阵营/小队，不要逐单位重复掷骰" if surprise else ""

    note_str = (
        f"系统已完成严谨计算：基础骰值(raw_roll)={raw_roll}，"
        f"{modifier_str}，优势状态={advantage_str}{surprise_str}，最终总值(final_total)={final_total}。\n"
        "【特别指令】：请向玩家如实播报这个算式（例：\"基础X + 修正Y = 最终Z\"），并严格仅使用 final_total 判断检定成败，不要自己重新做加法！"
    )

    return {
        "raw_roll": raw_roll,
        "modifier": modifier,
        "final_total": final_total,
        "advantage": advantage,
        "surprise": surprise,
        "status": "success",
        "note": note_str
    }


def _apply_advantage_formula(formula: str, advantage: Literal["normal", "advantage", "disadvantage"]) -> str:
    """常规 d20 掷骰在工具入口统一表达优势/劣势，突袭先攻也复用这一层。"""
    if formula != "1d20":
        return formula
    if advantage == "advantage":
        return "2d20kh1"
    if advantage == "disadvantage":
        return "2d20kl1"
    return formula
