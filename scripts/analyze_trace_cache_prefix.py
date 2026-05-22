"""分析 trace 中 DeepSeek KC 命中与相邻请求前缀断点。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _message_label(message: dict[str, Any]) -> str:
    role = message.get("role") or message.get("message_type") or "message"
    name = message.get("name")
    tool_calls = [call.get("name") for call in message.get("tool_calls") or []]
    suffix = f":{name}" if name else ""
    if tool_calls:
        suffix += f":tools={','.join(str(item) for item in tool_calls)}"
    return f"{role}{suffix}"


def _tool_label(tool: dict[str, Any]) -> str:
    return str(tool.get("name") or ((tool.get("function") or {}).get("name")) or "tool")


def _openai_message_from_trace(message: dict[str, Any]) -> dict[str, Any]:
    """把 trace 里的 LangChain message 投影成近似 OpenAI-compatible 请求消息。"""
    role = str(message.get("role") or "").lower()
    message_type = str(message.get("message_type") or "")
    if role == "human" or message_type == "HumanMessage":
        normalized_role = "user"
    elif role == "ai" or message_type == "AIMessage":
        normalized_role = "assistant"
    elif role == "tool" or message_type == "ToolMessage":
        normalized_role = "tool"
    else:
        normalized_role = role or "system"

    payload: dict[str, Any] = {
        "role": normalized_role,
        "content": message.get("content") or "",
    }
    if normalized_role == "tool":
        payload["tool_call_id"] = str(message.get("tool_call_id") or message.get("id") or "")
        if name := message.get("name"):
            payload["name"] = name
    if tool_calls := message.get("tool_calls"):
        payload["tool_calls"] = [_openai_tool_call_from_trace(tool_call) for tool_call in tool_calls]
    return payload


def _openai_tool_call_from_trace(tool_call: dict[str, Any]) -> dict[str, Any]:
    """工具调用参数按稳定 JSON 串表示，贴近 OpenAI tool_calls 形态。"""
    raw_args = tool_call.get("args", {})
    arguments = raw_args if isinstance(raw_args, str) else _stable_json(raw_args)
    return {
        "id": str(tool_call.get("id") or ""),
        "type": "function",
        "function": {
            "name": str(tool_call.get("name") or ""),
            "arguments": arguments,
        },
    }


def _openai_tool_schema_from_trace(tool: dict[str, Any]) -> dict[str, Any]:
    """只保留模型实际会看到的工具名、描述和参数结构，剔除 trace 附加字段。"""
    return {
        "type": "function",
        "function": {
            "name": _tool_label(tool),
            "description": str(tool.get("description") or ""),
            "parameters": tool.get("args_schema") or {},
        },
    }


def _openai_request_units(start_payload: dict[str, Any]) -> list[dict[str, str]]:
    """按近似实际请求顺序拆分成可比较单元。"""
    units = [
        {
            "kind": "message",
            "label": "messages[0] system",
            "text": _stable_json({"role": "system", "content": str(start_payload.get("system_prompt", ""))}),
        }
    ]
    for index, message in enumerate(start_payload.get("messages") or [], start=1):
        normalized = _openai_message_from_trace(message)
        units.append(
            {
                "kind": "message",
                "label": f"messages[{index}] {_message_label(normalized)}",
                "text": _stable_json(normalized),
            }
        )

    for index, tool in enumerate(start_payload.get("available_tools") or []):
        schema = _openai_tool_schema_from_trace(tool)
        units.append(
            {
                "kind": "tool",
                "label": f"tools[{index}] {_tool_label(tool)}",
                "text": _stable_json(schema),
            }
        )
    return units


def _prompt_units(start_payload: dict[str, Any]) -> list[dict[str, str]]:
    """旧 trace 形态比较仅作辅助；KC 判断优先看 _openai_request_units。"""
    units = [
        {
            "kind": "system",
            "label": "system_prompt",
            "text": str(start_payload.get("system_prompt", "")),
        },
        {
            "kind": "tools",
            "label": "available_tools",
            "text": _stable_json(start_payload.get("available_tools", [])),
        },
    ]
    for index, message in enumerate(start_payload.get("messages") or []):
        units.append(
            {
                "kind": "message",
                "label": f"messages[{index}] {_message_label(message)}",
                "text": _stable_json(message),
            }
        )
    return units


def _common_prefix_chars(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


def _first_changed_unit(left_units: list[dict[str, str]], right_units: list[dict[str, str]]) -> dict[str, Any]:
    limit = min(len(left_units), len(right_units))
    for index in range(limit):
        left = left_units[index]
        right = right_units[index]
        if left["kind"] != right["kind"] or left["label"] != right["label"] or left["text"] != right["text"]:
            return {
                "index": index,
                "left": _unit_summary(left),
                "right": _unit_summary(right),
                "common_chars_inside_unit": _common_prefix_chars(left["text"], right["text"]),
            }
    if len(left_units) != len(right_units):
        side = left_units if len(left_units) > len(right_units) else right_units
        return {
            "index": limit,
            "left": _unit_summary(left_units[limit]) if len(left_units) > limit else None,
            "right": _unit_summary(right_units[limit]) if len(right_units) > limit else None,
            "common_chars_inside_unit": 0,
            "length_changed": True,
        }
    return {"index": None}


def _unit_summary(unit: dict[str, str]) -> dict[str, Any]:
    text = unit["text"]
    return {
        "kind": unit["kind"],
        "label": unit["label"],
        "chars": len(text),
        "hash": _hash_text(text),
        "preview": text[:180].replace("\n", " "),
    }


def _usage_from_completion(completion_payload: dict[str, Any]) -> dict[str, Any]:
    response = completion_payload.get("response") or {}
    metadata = response.get("response_metadata") or {}
    usage = metadata.get("token_usage") or {}
    usage_metadata = response.get("usage_metadata") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if hit is None:
        hit = (usage_metadata.get("input_token_details") or {}).get("cache_read")
    prompt = usage.get("prompt_tokens") or usage_metadata.get("input_tokens")
    return {
        "prompt_tokens": prompt,
        "cache_hit_tokens": hit,
        "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
        "completion_tokens": usage.get("completion_tokens") or usage_metadata.get("output_tokens"),
    }


def _load_invocations(trace_path: Path) -> list[dict[str, Any]]:
    started: dict[str, dict[str, Any]] = {}
    invocations: list[dict[str, Any]] = []
    for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload") or {}
        invocation_id = payload.get("invocation_id")
        if event.get("event_type") == "llm_invocation_started" and invocation_id:
            started[invocation_id] = {"line": line_number, "event": event, "payload": payload}
        elif event.get("event_type") == "llm_invocation_completed" and invocation_id:
            item = started.get(invocation_id, {"payload": {}})
            item["completed_line"] = line_number
            item["completed_payload"] = payload
            invocations.append(item)
    return invocations


def analyze_trace(trace_path: Path, *, mode: str | None = None, limit: int | None = None) -> int:
    if not trace_path.exists():
        print(f"trace file not found: {trace_path}")
        return 1

    invocations = _load_invocations(trace_path)
    if mode:
        invocations = [item for item in invocations if item["payload"].get("mode") == mode]
    if limit:
        invocations = invocations[-limit:]

    previous_units: list[dict[str, str]] | None = None
    previous_payload: dict[str, Any] | None = None
    print(f"trace: {trace_path}")
    print(f"invocations: {len(invocations)}")
    print()

    for index, item in enumerate(invocations, start=1):
        payload = item["payload"]
        completed_payload = item.get("completed_payload") or {}
        units = _openai_request_units(payload)
        full_text = _stable_json([{"kind": unit["kind"], "label": unit["label"], "text": unit["text"]} for unit in units])
        usage = _usage_from_completion(completed_payload)
        prompt_tokens = usage.get("prompt_tokens") or 0
        hit_tokens = usage.get("cache_hit_tokens") or 0
        hit_ratio = hit_tokens / prompt_tokens if prompt_tokens else 0
        messages = payload.get("messages") or []
        tools = payload.get("available_tools") or []
        runtime_indexes = [
            message_index
            for message_index, message in enumerate(messages)
            if "<runtime_state" in str(message.get("content", ""))
        ]
        old_combat_archive_count = sum(1 for message in messages if "[系统:战斗归档]" in str(message.get("content", "")))
        response = (completed_payload.get("response") or {})
        tool_calls = [call.get("name") for call in response.get("tool_calls") or []]

        print(
            f"#{index} start_line={item.get('line')} done_line={item.get('completed_line')} "
            f"mode={payload.get('mode')} phase={payload.get('phase')} "
            f"messages={len(messages)} tools={len(tools)} prompt_chars={len(full_text)} "
            f"prompt_tokens={prompt_tokens} hit={hit_tokens} miss={usage.get('cache_miss_tokens')} "
            f"hit_ratio={hit_ratio:.2%} runtime={runtime_indexes} old_combat_archives={old_combat_archive_count} "
            f"tool_calls={tool_calls}"
        )
        print(f"   tool_names={', '.join(_tool_label(tool) for tool in tools)}")

        if previous_units is not None:
            change = _first_changed_unit(previous_units, units)
            previous_text = _stable_json(
                [{"kind": unit["kind"], "label": unit["label"], "text": unit["text"]} for unit in previous_units]
            )
            common_chars = _common_prefix_chars(previous_text, full_text)
            common_ratio = common_chars / max(min(len(previous_text), len(full_text)), 1)
            print(f"   openai_payload_common_prefix: chars={common_chars} ratio={common_ratio:.2%}")
            if change.get("index") is None:
                print("   first_change_vs_previous: none")
            else:
                print(f"   first_change_vs_previous: unit_index={change['index']} common_chars_inside_unit={change.get('common_chars_inside_unit')}")
                print(f"      prev={change.get('left')}")
                print(f"      curr={change.get('right')}")

        previous_units = units
        previous_payload = payload
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="trace jsonl 路径")
    parser.add_argument("--mode", choices=["narrative", "combat", "memory_summary"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="只分析最后 N 次 LLM 调用")
    args = parser.parse_args()
    return analyze_trace(args.trace, mode=args.mode, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
