"""把每次 agent 交互落成结构化 trace 文件，并支持离线导出。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage

from app.config.settings import settings


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TRACE_LOCK = threading.Lock()


def _now_iso() -> str:
    """统一输出本地时区的毫秒级时间戳，便于按交互排查。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _json_safe(value: Any) -> Any:
    """把运行时对象投影成稳定 JSON，避免 trace 因对象不可序列化而失败。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, BaseMessage):
        return serialize_message(value)

    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))

    return str(value)


def _safe_file_stem(raw_value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", raw_value).strip("._")
    return cleaned or "session"


def resolve_trace_dir(trace_dir: str | Path | None = None) -> Path:
    """统一解析 trace 根目录，允许通过配置或脚本参数覆盖。"""
    if trace_dir is not None:
        resolved = Path(trace_dir)
    else:
        configured_dir = (settings.agent_trace_dir or "").strip()
        resolved = Path(configured_dir) if configured_dir else Path("logs/agent_traces")

    if not resolved.is_absolute():
        resolved = _BACKEND_ROOT / resolved

    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_trace_file(session_id: str, trace_dir: str | Path | None = None) -> Path:
    return resolve_trace_dir(trace_dir) / f"{_safe_file_stem(session_id)}.jsonl"


def resolve_export_dir(export_dir: str | Path | None = None) -> Path:
    resolved = Path(export_dir) if export_dir is not None else _BACKEND_ROOT / "logs" / "trace_exports"
    if not resolved.is_absolute():
        resolved = _BACKEND_ROOT / resolved
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    """把 LangChain message 还原成可落盘的完整结构。"""
    payload: dict[str, Any] = {
        "id": getattr(message, "id", None),
        "message_type": message.__class__.__name__,
        "role": getattr(message, "type", message.__class__.__name__.replace("Message", "").lower()),
        "content": _json_safe(getattr(message, "content", None)),
    }

    if getattr(message, "name", None):
        payload["name"] = getattr(message, "name")

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = _json_safe(tool_calls)

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if additional_kwargs:
        payload["additional_kwargs"] = _json_safe(additional_kwargs)

    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        payload["response_metadata"] = _json_safe(response_metadata)

    return payload


def serialize_tool(tool: Any) -> dict[str, Any]:
    """工具 schema 也属于模型输入的一部分，这里保留最关键的可见信息。"""
    payload: dict[str, Any] = {
        "name": getattr(tool, "name", tool.__class__.__name__),
        "description": getattr(tool, "description", ""),
    }

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        payload["args_schema"] = _json_safe(args_schema.model_json_schema())
    elif hasattr(tool, "tool_call_schema") and hasattr(tool.tool_call_schema, "model_json_schema"):
        payload["args_schema"] = _json_safe(tool.tool_call_schema.model_json_schema())

    return payload


def append_trace_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """按 session 追加 JSONL 事件；trace 失败不能影响主流程。"""
    if not settings.agent_trace_enabled or not session_id:
        return None

    trace_file = resolve_trace_file(session_id, trace_dir)
    event = {
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": _now_iso(),
        "payload": _json_safe(payload),
    }

    with _TRACE_LOCK:
        with trace_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    return trace_file


def trace_chat_request(
    session_id: str,
    *,
    entrypoint: str,
    message: str | None,
    resume_action: str | None,
    reaction_response: dict[str, Any] | None,
    pending_before_run: dict[str, Any] | None,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """记录一次用户侧入口，便于把用户输入时间与后续 LLM 调用串起来。"""
    return append_trace_event(
        session_id,
        "chat_request",
        {
            "entrypoint": entrypoint,
            "message": message,
            "resume_action": resume_action,
            "reaction_response": reaction_response,
            "pending_before_run": pending_before_run,
        },
        trace_dir=trace_dir,
    )


def trace_chat_result(
    session_id: str,
    *,
    entrypoint: str,
    reply: str,
    pending_action: dict[str, Any] | None,
    new_message_count: int,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """记录本轮对外可见结果，导出时可直接看到用户最终收到什么。"""
    return append_trace_event(
        session_id,
        "chat_result",
        {
            "entrypoint": entrypoint,
            "reply": reply,
            "pending_action": pending_action,
            "new_message_count": new_message_count,
        },
        trace_dir=trace_dir,
    )


def trace_chat_error(
    session_id: str,
    *,
    entrypoint: str,
    error: str,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """把请求阶段的拒绝或异常也写入 trace，避免排查时只看到成功分支。"""
    return append_trace_event(
        session_id,
        "chat_error",
        {
            "entrypoint": entrypoint,
            "error": error,
        },
        trace_dir=trace_dir,
    )


def start_llm_trace(
    session_id: str,
    *,
    mode: str,
    phase: str | None,
    system_prompt: str,
    hud_text: str,
    messages: list[BaseMessage],
    tools: list[Any],
    trace_dir: str | Path | None = None,
) -> tuple[str, str]:
    """记录一次模型调用前的完整上下文。"""
    invocation_id = str(uuid4())
    started_at = _now_iso()
    append_trace_event(
        session_id,
        "llm_invocation_started",
        {
            "invocation_id": invocation_id,
            "started_at": started_at,
            "mode": mode,
            "phase": phase,
            "system_prompt": system_prompt,
            "hud_text": hud_text,
            "messages": [serialize_message(message) for message in messages],
            "available_tools": [serialize_tool(tool) for tool in tools],
        },
        trace_dir=trace_dir,
    )
    return invocation_id, started_at


def finish_llm_trace(
    session_id: str,
    *,
    invocation_id: str,
    started_at: str,
    duration_ms: float,
    mode: str,
    phase: str | None,
    response: BaseMessage,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """记录模型响应全文和耗时。"""
    return append_trace_event(
        session_id,
        "llm_invocation_completed",
        {
            "invocation_id": invocation_id,
            "started_at": started_at,
            "completed_at": _now_iso(),
            "duration_ms": round(duration_ms, 3),
            "mode": mode,
            "phase": phase,
            "response": serialize_message(response),
        },
        trace_dir=trace_dir,
    )


def fail_llm_trace(
    session_id: str,
    *,
    invocation_id: str,
    started_at: str,
    duration_ms: float,
    mode: str,
    phase: str | None,
    error: Exception,
    trace_dir: str | Path | None = None,
) -> Path | None:
    """模型调用失败时保留错误上下文，避免只有栈信息没有 prompt。"""
    return append_trace_event(
        session_id,
        "llm_invocation_failed",
        {
            "invocation_id": invocation_id,
            "started_at": started_at,
            "failed_at": _now_iso(),
            "duration_ms": round(duration_ms, 3),
            "mode": mode,
            "phase": phase,
            "error_type": error.__class__.__name__,
            "error": str(error),
        },
        trace_dir=trace_dir,
    )


def load_trace_events(session_id: str, trace_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """读取单个 session 的 JSONL trace。"""
    trace_file = resolve_trace_file(session_id, trace_dir)
    if not trace_file.exists():
        return []

    events: list[dict[str, Any]] = []
    with trace_file.open("r", encoding="utf-8") as file:
        for line in file:
            raw_line = line.strip()
            if not raw_line:
                continue
            events.append(json.loads(raw_line))
    return events


def detect_latest_session_id(trace_dir: str | Path | None = None) -> str | None:
    """导出脚本默认取最近改动的 trace 会话，减少手工查 session id。"""
    trace_root = resolve_trace_dir(trace_dir)
    trace_files = sorted(trace_root.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    for trace_file in trace_files:
        with trace_file.open("r", encoding="utf-8") as file:
            first_line = file.readline().strip()
        if not first_line:
            continue
        record = json.loads(first_line)
        session_id = record.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


def _json_block(payload: Any) -> str:
    return json.dumps(_json_safe(payload), ensure_ascii=False, indent=2)


def render_trace_markdown(session_id: str, events: list[dict[str, Any]]) -> str:
    """把结构化 trace 渲染成人可读 markdown，方便直接打开文件排查。"""
    exported_at = _now_iso()
    lines = [
        "# Agent Trace Export",
        "",
        f"- Session ID: {session_id}",
        f"- Exported At: {exported_at}",
        f"- Event Count: {len(events)}",
        "",
    ]

    for index, event in enumerate(events, start=1):
        event_type = event.get("event_type", "unknown")
        timestamp = event.get("timestamp", "")
        payload = event.get("payload", {})

        lines.append(f"## {index}. {event_type}")
        lines.append("")
        lines.append(f"- Timestamp: {timestamp}")

        if event_type == "llm_invocation_started":
            lines.append(f"- Invocation ID: {payload.get('invocation_id', '')}")
            lines.append(f"- Mode: {payload.get('mode', '')}")
            lines.append(f"- Phase: {payload.get('phase', '')}")
            lines.append("")
            lines.append("### System Prompt")
            lines.append("")
            lines.append("```text")
            lines.append(str(payload.get("system_prompt", "")))
            lines.append("```")
            lines.append("")
            lines.append("### HUD")
            lines.append("")
            lines.append("```text")
            lines.append(str(payload.get("hud_text", "")))
            lines.append("```")
            lines.append("")
            lines.append("### Model Input Messages")
            lines.append("")
            lines.append("```json")
            lines.append(_json_block(payload.get("messages", [])))
            lines.append("```")
            lines.append("")
            lines.append("### Available Tools")
            lines.append("")
            lines.append("```json")
            lines.append(_json_block(payload.get("available_tools", [])))
            lines.append("```")
        elif event_type == "llm_invocation_completed":
            lines.append(f"- Invocation ID: {payload.get('invocation_id', '')}")
            lines.append(f"- Duration: {payload.get('duration_ms', '')} ms")
            lines.append("")
            lines.append("### Response")
            lines.append("")
            lines.append("```json")
            lines.append(_json_block(payload.get("response", {})))
            lines.append("```")
        else:
            lines.append("")
            lines.append("```json")
            lines.append(_json_block(payload))
            lines.append("```")

        lines.append("")

    return "\n".join(lines).strip() + "\n"


def export_trace_report(
    session_id: str,
    events: list[dict[str, Any]],
    *,
    output_path: str | Path | None = None,
    output_format: str = "markdown",
) -> Path:
    """把 trace 导出成 markdown 或 JSON 文件。"""
    normalized_format = output_format.lower()
    if normalized_format not in {"markdown", "json"}:
        raise ValueError(f"Unsupported output format: {output_format}")

    if output_path is None:
        suffix = "md" if normalized_format == "markdown" else "json"
        filename = f"{_safe_file_stem(session_id)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{suffix}"
        target = resolve_export_dir() / filename
    else:
        target = Path(output_path)
        if not target.is_absolute():
            target = resolve_export_dir() / target
        target.parent.mkdir(parents=True, exist_ok=True)

    if normalized_format == "markdown":
        rendered = render_trace_markdown(session_id, events)
    else:
        rendered = json.dumps(events, ensure_ascii=False, indent=2)

    target.write_text(rendered, encoding="utf-8")
    return target