"""常规掷骰工具测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import d20


backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 LangChain ToolCall 格式调用含注入参数的工具。"""
    tool_call = {
        "name": tool_func.name,
        "args": tool_input,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool_func.invoke(tool_call)


def test_request_dice_roll_supports_disadvantage_for_surprise_context():
    from app.services.tools.dice_tools import request_dice_roll

    rolled_exprs: list[str] = []
    real_roll = d20.roll

    def fake_roll(expr: str):
        rolled_exprs.append(expr)
        return real_roll("2d20kl1")

    with patch("app.services.tools.dice_tools.d20.roll", side_effect=fake_roll):
        result = _invoke_tool(
            request_dice_roll,
            tool_input={
                "reason": "突袭先攻",
                "ability": "dex",
                "advantage": "disadvantage",
                "surprise": True,
                "state": {"player": {"modifiers": {"dex": 2}}},
            },
        )

    payload = json.loads(result.content)
    assert rolled_exprs == ["2d20kl1"]
    assert payload["modifier"] == 2
    assert payload["advantage"] == "disadvantage"
    assert payload["surprise"] is True
    assert "新版突袭先攻" in payload["note"]
    assert "不要逐单位重复掷骰" in payload["note"]
