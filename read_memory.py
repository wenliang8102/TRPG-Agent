import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

# 把 backend 目录加到 Python 路径，便于直接复用项目内的记忆实现。
sys.path.append(str(Path(__file__).parent / "backend"))

from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.memory.episodic_store import EpisodicStore


def _resolve_db_path(db_path: str) -> Path:
    path = Path(db_path)
    if path.is_absolute():
        return path
    return Path(__file__).parent / path


def _list_session_ids(db_path: Path, limit: int, target_session_id: str | None) -> list[str]:
    if target_session_id:
        return [target_session_id]

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    session_ids: list[str] = []

    try:
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id DESC LIMIT ?", (limit,))
        session_ids.extend(row[0] for row in cur.fetchall() if row and row[0])
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute("SELECT DISTINCT session_id FROM episodic_memory ORDER BY session_id DESC LIMIT ?", (limit,))
        for row in cur.fetchall():
            if row and row[0] and row[0] not in session_ids:
                session_ids.append(row[0])
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return session_ids[:limit]


def _message_content_preview(message) -> str:
    content = str(getattr(message, "content", "")).strip()
    return content[:200] + "..." if len(content) > 200 else content


def _print_checkpoint_snapshot(channel_values: dict) -> None:
    summary = channel_values.get("conversation_summary", "")
    episodic_context = channel_values.get("episodic_context", []) or []

    if episodic_context:
        print("  🧠 [当前注入到热路径的情节记忆]:")
        for item in episodic_context:
            print(f"    - {item}")
    elif summary:
        print(f"  ✨ [前情提要 fallback]:\n{summary}\n")
    else:
        print("  ⚠️ [长期记忆注入] 当前为空\n")

    messages = channel_values.get("messages", [])
    print(f"  💬 [当前流转的真实消息窗口] (共保留 {len(messages)} 条):")
    for message in messages:
        role = message.__class__.__name__.replace("Message", "")
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tool_call in message.tool_calls:
                print(f"    [AI 🛠️ 调用工具 -> {tool_call.get('name', 'UnknownTool')}]: 参数 {tool_call.get('args', {})}")

        preview = _message_content_preview(message)
        if not preview:
            continue

        if role == "Tool":
            tool_name = getattr(message, "name", "未知工具")
            print(f"    [{role} ⚙️ ({tool_name})]: {preview}")
        else:
            print(f"    [{role}]: {preview}")


def _print_episodic_records(records: list[dict], context_blocks: list[str]) -> None:
    if context_blocks:
        print("  📚 [按近期摘要 + 稳定事件混合得到的上下文块]:")
        for block in context_blocks:
            print(f"    - {block}")
    else:
        print("  📚 [情节上下文块] 当前为空")

    if not records:
        print("  🗃️ [episodic_memory] 当前没有记录")
        return

    print(f"  🗃️ [episodic_memory 最近 {len(records)} 条记录]:")
    for record in records:
        record_kind = record.get("record_kind", "unknown")
        payload = record.get("payload", {})
        turn_id = record.get("turn_id", "")
        created_at = record.get("created_at", "")
        print(f"    - ({created_at}) turn={turn_id} kind={record_kind}")

        if record_kind == "turn_summary":
            print(f"      summary: {payload.get('summary', '')}")
        elif record_kind == "stable_events":
            for event in payload.get("events", []):
                print(f"      event: {json.dumps(event, ensure_ascii=False)}")
        elif record_kind == "turn_messages":
            messages = payload.get("messages", [])
            print(f"      messages: {len(messages)} 条")
            for message in messages[:3]:
                print(f"        * {message.get('role')}/{message.get('kind')}: {message.get('content', '')}")
        else:
            print(f"      payload: {json.dumps(payload, ensure_ascii=False)}")


async def read_memory(db_path: str, limit: int = 5, session_id: str | None = None) -> None:
    """同时读取 checkpoint 与 episodic_memory，便于核对当前上下文链路。"""
    db_file = _resolve_db_path(db_path)
    episodic_store = EpisodicStore(str(db_file))

    try:
        saver = await get_checkpointer(str(db_file))
        await episodic_store.setup()
        session_ids = _list_session_ids(db_file, limit=limit, target_session_id=session_id)

        if not session_ids:
            print("未在数据库中找到任何 checkpoint 或 episodic 记录。")
            return

        print(f"找到 {len(session_ids)} 个待检查会话，正在输出诊断信息...\n")

        for index, current_session_id in enumerate(session_ids, start=1):
            print(f"[{index}] 会话 ID: {current_session_id}")
            config = {"configurable": {"thread_id": current_session_id}}
            checkpoint_tuple = await saver.aget_tuple(config)

            if checkpoint_tuple:
                channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
                _print_checkpoint_snapshot(channel_values)
            else:
                print("  ⚠️ [checkpoint] 当前没有快照")

            context_blocks = await episodic_store.fetch_recent_context_blocks(current_session_id)
            records = await episodic_store.fetch_recent_records(current_session_id, limit=8)
            _print_episodic_records(records, context_blocks)
            print("-" * 72)

    except Exception as exc:
        print(f"读取数据库或解析存档失败: {exc}")
    finally:
        await episodic_store.close()
        await close_checkpointer()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查看 checkpoint 与 episodic_memory 的调试快照")
    parser.add_argument("--db-path", default="backend/data/context_memory.sqlite3", help="SQLite 数据库路径")
    parser.add_argument("--limit", type=int, default=5, help="最多展示多少个会话")
    parser.add_argument("--session-id", default=None, help="只查看指定 session/thread")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(read_memory(args.db_path, limit=args.limit, session_id=args.session_id))