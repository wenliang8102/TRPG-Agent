"""根据玩家可见对话生成下一轮回复候选。"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from app.services.llm_service import LLMService


REPLY_SUGGESTION_SYSTEM_PROMPT = """你为桌面角色扮演游戏中的玩家生成下一句可选回复。

生成恰好三条彼此明显不同的候选。候选可以是对白、调查、移动或其他当前即可执行的行动，但必须：
- 使用玩家口吻，能够原样作为玩家消息发送；
- 只依据已展示的对话与玩家公开身份，不推断隐藏剧情、敌人信息或检定结果；
- 保留玩家决策权，不替玩家断言情绪、成功结果或尚未发生的事实；
- 简洁自然，不解释候选用途，不使用编号，不提及系统、工具、参数或内部规则字段。

若上下文包含当前战斗局面，还必须：
- 只建议当前行动者此刻可以尝试的战术意图，遵守剩余动作、附赠动作、移动、资源与距离限制；
- 当前行动者是玩家时使用第一人称；当前行动者是友方时，写成玩家对该友方的简短战术指令；
- 不建议攻击已倒下目标，不虚构未列出的武器、法术、物品或能力；
- 主要动作已经用尽时，不再建议需要主要动作的行为，并允许建议结束回合。
"""


class GeneratedReplySuggestions(BaseModel):
    """模型边界要求固定三条非空且不重复的候选。"""

    suggestions: list[str] = Field(min_length=3, max_length=3)

    @field_validator("suggestions")
    @classmethod
    def normalize_suggestions(cls, suggestions: list[str]) -> list[str]:
        normalized = [suggestion.strip() for suggestion in suggestions]
        if any(not suggestion for suggestion in normalized):
            raise ValueError("Reply suggestions cannot be empty.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Reply suggestions must be distinct.")
        return normalized


class ReplySuggestionService:
    """把可信的可见上下文投影为不具备业务权威性的玩家措辞建议。"""

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self._llm_service = llm_service or LLMService()

    async def generate(
        self,
        *,
        conversation: list[dict[str, str]],
        player_identity: dict[str, str],
        combat_context: dict | None = None,
    ) -> list[str]:
        context = json.dumps(
            {
                "player": player_identity,
                "conversation": conversation,
                "combat": combat_context,
            },
            ensure_ascii=False,
        )
        result = await self._llm_service.ainvoke_structured(
            [
                SystemMessage(content=REPLY_SUGGESTION_SYSTEM_PROMPT),
                HumanMessage(content=context),
            ],
            GeneratedReplySuggestions,
        )
        return result.suggestions
