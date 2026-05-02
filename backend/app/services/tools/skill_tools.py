"""技能加载工具：按需暴露复杂工具说明，避免系统提示常驻大段教程。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from app.services.skills import load_skill_content


@tool
def load_skill(
    skill_id: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """加载一个项目内技能的完整说明。复杂工具使用前先调用本工具获取对应技能。"""
    content = load_skill_content(skill_id)
    return Command(update={"messages": [
        ToolMessage(content=content, tool_call_id=tool_call_id)
    ]})
