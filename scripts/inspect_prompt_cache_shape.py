"""检查模型上下文是否形成适合 DeepSeek Context Caching 的稳定前缀。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.memory.context_assembler import (
    CONTEXT_HARD_TRIM_TOKEN_BUDGET,
    CONTEXT_SOFT_COMPACT_TOKEN_BUDGET,
    MODEL_CONTEXT_TOKEN_LIMIT,
    ContextAssembler,
    build_runtime_state_message,
    estimate_messages_tokens,
)
from app.prompts import get_assistant_system_prompt
from app.services.tools import get_tool_profile


def stable_history_pairs(count: int) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for index in range(count):
        messages.append(HumanMessage(content=f"我继续谨慎探索第 {index + 1} 段通道，留意脚印、气味和声音。"))
        messages.append(AIMessage(content=f"第 {index + 1} 段通道保持潮湿昏暗，你记录下可疑痕迹但暂未触发危险。", tool_calls=[]))
    return messages


def build_narrative_pair(history_pairs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    base_messages = [
        *stable_history_pairs(history_pairs),
        HumanMessage(content="我走进洞穴入口。"),
        AIMessage(content="洞口潮湿阴冷，远处传来水声。", tool_calls=[]),
        HumanMessage(content="我检查地上的脚印。"),
    ]
    state_a = {
        "phase": "exploration",
        "conversation_summary": "玩家抵达洞穴入口，正在寻找线索。",
        "messages": base_messages,
        "player": {"id": "player_hero", "name": "艾拉", "side": "player", "hp": 12, "max_hp": 12, "ac": 13},
        "adventure": {"active_node_id": "cave_entrance", "module_id": "lost_mine"},
        "space": {
            "active_map_id": "cave_entrance",
            "maps": {"cave_entrance": {"id": "cave_entrance", "name": "洞穴入口", "width": 80, "height": 60, "grid_size": 5}},
            "placements": {"player_hero": {"unit_id": "player_hero", "map_id": "cave_entrance", "position": {"x": 10, "y": 15}}},
        },
    }
    state_b = {
        **state_a,
        "messages": [
            *base_messages,
            AIMessage(content="", tool_calls=[{"name": "request_dice_roll", "args": {"reason": "生存检定", "formula": "1d20+3"}, "id": "call_1"}]),
            ToolMessage(content='{"raw_roll": 14, "final_total": 17}', tool_call_id="call_1", name="request_dice_roll"),
            AIMessage(content="你找到了一串新鲜的小型脚印，朝洞穴深处延伸。", tool_calls=[]),
            HumanMessage(content="我沿着脚印前进。"),
        ],
        "conversation_summary": "玩家抵达洞穴入口，发现小型脚印通往洞穴深处。",
        "space": {
            "active_map_id": "cave_entrance",
            "maps": {"cave_entrance": {"id": "cave_entrance", "name": "洞穴入口", "width": 80, "height": 60, "grid_size": 5}},
            "placements": {"player_hero": {"unit_id": "player_hero", "map_id": "cave_entrance", "position": {"x": 20, "y": 15}}},
        },
    }
    return state_a, state_b


def build_combat_pair(history_pairs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    base_messages = [
        *stable_history_pairs(history_pairs),
        HumanMessage(content="继续战斗。"),
        AIMessage(content="", tool_calls=[{"name": "use_monster_action", "args": {"actor_id": "goblin_1", "action_id": "scimitar"}, "id": "call_1"}]),
        ToolMessage(content="Goblin 使用 Scimitar 攻击，英雄 HP: 12 -> 8", tool_call_id="call_1", name="use_monster_action"),
        AIMessage(content="", tool_calls=[{"name": "next_turn", "args": {}, "id": "call_2"}]),
        ToolMessage(content="进入英雄的回合。", tool_call_id="call_2", name="next_turn"),
        HumanMessage(content="我攻击哥布林。"),
    ]
    combat = {
        "round": 2,
        "current_actor_id": "player_hero",
        "initiative_order": ["goblin_1", "player_hero"],
        "participants": {
            "goblin_1": {
                "id": "goblin_1",
                "name": "Goblin",
                "side": "enemy",
                "hp": 7,
                "max_hp": 7,
                "ac": 15,
                "actions": [{"id": "scimitar", "name": "Scimitar", "kind": "attack"}],
            }
        },
    }
    state_a = {
        "phase": "combat",
        "scene_summary": "哥布林守着洞穴窄口。",
        "messages": base_messages,
        "active_combat_message_start": history_pairs * 2,
        "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 8, "max_hp": 12, "ac": 14, "attacks": [{"name": "Longsword"}]},
        "combat": combat,
    }
    state_b = {
        **state_a,
        "messages": [
            *base_messages,
            AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {"attacker_id": "player_hero", "target_id": "goblin_1"}, "id": "call_3"}]),
            ToolMessage(content="英雄攻击 Goblin，Goblin HP: 7 -> 2", tool_call_id="call_3", name="attack_action"),
            AIMessage(content="", tool_calls=[{"name": "next_turn", "args": {}, "id": "call_4"}]),
            ToolMessage(content="进入 Goblin 的回合。", tool_call_id="call_4", name="next_turn"),
            HumanMessage(content="继续。"),
        ],
        "active_combat_message_start": history_pairs * 2,
        "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 8, "max_hp": 12, "ac": 14, "attacks": [{"name": "Longsword"}]},
        "combat": {
            **combat,
            "current_actor_id": "goblin_1",
            "participants": {
                "goblin_1": {
                    "id": "goblin_1",
                    "name": "Goblin",
                    "side": "enemy",
                    "hp": 2,
                    "max_hp": 7,
                    "ac": 15,
                    "actions": [{"id": "scimitar", "name": "Scimitar", "kind": "attack"}],
                }
            },
        },
    }
    return state_a, state_b


def assemble_payload(state: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    assembler = ContextAssembler()
    assembled = assembler.assemble(state, mode, base_system_prompt=get_assistant_system_prompt(mode))
    messages = [
        SystemMessage(content=assembled.system_prompt),
        *assembled.model_input_messages,
        build_runtime_state_message(assembled.runtime_state_text),
    ]
    payload = [serialize_message(message) for message in messages]
    payload.append(
        {
            "role": "tool_schema_digest",
            "content": "\n".join(f"{tool.name}: {tool.description}" for tool in get_tool_profile(mode)),
        }
    )
    return payload


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    role = getattr(message, "type", message.__class__.__name__.replace("Message", "").lower())
    payload: dict[str, Any] = {"role": role, "content": str(getattr(message, "content", ""))}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = message.tool_call_id
        payload["name"] = message.name
    return payload


def payload_text(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


def runtime_state_index(payload: list[dict[str, Any]]) -> int:
    for index, message in enumerate(payload):
        if "<runtime_state" in message.get("content", ""):
            return index
    return -1


def inspect(mode: str, min_prefix_chars: int, history_pairs: int) -> int:
    state_a, state_b = build_combat_pair(history_pairs) if mode == COMBAT_AGENT_MODE else build_narrative_pair(history_pairs)
    payload_a = assemble_payload(state_a, mode)
    payload_b = assemble_payload(state_b, mode)
    text_a = payload_text(payload_a)
    text_b = payload_text(payload_b)
    prefix_len = common_prefix_len(text_a, text_b)
    runtime_index_a = runtime_state_index(payload_a)
    runtime_index_b = runtime_state_index(payload_b)
    runtime_before_tool_schema = runtime_index_a == len(payload_a) - 2 and runtime_index_b == len(payload_b) - 2
    prefix_ratio = prefix_len / max(min(len(text_a), len(text_b)), 1)

    print(f"mode: {mode}")
    print(f"stable_history_pairs: {history_pairs}")
    print(f"context_token_limit: {MODEL_CONTEXT_TOKEN_LIMIT}")
    print(f"soft_compact_token_budget: {CONTEXT_SOFT_COMPACT_TOKEN_BUDGET}")
    print(f"hard_trim_token_budget: {CONTEXT_HARD_TRIM_TOKEN_BUDGET}")
    print(f"estimated_history_tokens_a: {estimate_messages_tokens(state_a['messages'])}")
    print(f"estimated_history_tokens_b: {estimate_messages_tokens(state_b['messages'])}")
    print(f"payload_a_chars: {len(text_a)}")
    print(f"payload_b_chars: {len(text_b)}")
    print(f"common_prefix_chars: {prefix_len}")
    print(f"common_prefix_ratio: {prefix_ratio:.3f}")
    print(f"runtime_state_message_index_a: {runtime_index_a}/{len(payload_a) - 1}")
    print(f"runtime_state_message_index_b: {runtime_index_b}/{len(payload_b) - 1}")
    print(f"runtime_state_before_tool_schema: {runtime_before_tool_schema}")

    if prefix_len < min_prefix_chars:
        print(f"FAIL: common prefix is below {min_prefix_chars} chars")
        return 1
    if not runtime_before_tool_schema:
        print("FAIL: runtime state is not the final model message before tool schema")
        return 1
    print("PASS: context shape is cache-friendly")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[NARRATIVE_AGENT_MODE, COMBAT_AGENT_MODE], default=COMBAT_AGENT_MODE)
    parser.add_argument("--min-prefix-chars", type=int, default=1000)
    parser.add_argument("--history-pairs", type=int, default=8)
    args = parser.parse_args()
    return inspect(args.mode, args.min_prefix_chars, args.history_pairs)


if __name__ == "__main__":
    raise SystemExit(main())
