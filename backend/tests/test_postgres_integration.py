"""PostgreSQL 集成测试；默认跳过，设置 TRPG_TEST_POSTGRES_URL 后运行。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from app.adventures.rewards import claim_pending_reward
from app.adventures.runtime import AdventureProgressDecision, adjudicate_and_apply_adventure_progress
from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.services.session_store import (
    create_chat_session,
    list_chat_sessions,
    purge_chat_session_data,
    touch_chat_session,
)
from app.utils.agent_trace import resolve_trace_file


POSTGRES_URL = os.getenv("TRPG_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TRPG_TEST_POSTGRES_URL is not set")


class CounterState(TypedDict, total=False):
    count: int


class FakeDirector:
    """让 runtime 测试只验证状态规则，不依赖外部 LLM。"""

    def __init__(self, decision: AdventureProgressDecision) -> None:
        self.decision = decision

    def adjudicate(self, *, state, recent_messages, session_id=None):
        return self.decision


@pytest.fixture
def anyio_backend() -> str:
    """psycopg async 在 Windows 下走 Selector 策略，测试固定 asyncio 后端。"""
    return "asyncio"


@pytest.fixture
def postgres_url() -> str:
    assert POSTGRES_URL is not None
    return POSTGRES_URL


@pytest.fixture(autouse=True)
def _clean_postgres_test_rows(postgres_url: str):
    """每个测试前后只清理测试名前缀，避免误删人工验证数据。"""
    _delete_test_rows(postgres_url)
    yield
    _delete_test_rows(postgres_url)


def _delete_test_rows(postgres_url: str) -> None:
    with psycopg.connect(postgres_url) as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE thread_id LIKE 'pg-it-%'")
        if _table_exists(conn, "app_chat_sessions"):
            conn.execute("DELETE FROM app_chat_sessions WHERE id LIKE 'pg-it-%'")
        if _table_exists(conn, "episodic_memory"):
            conn.execute("DELETE FROM episodic_memory WHERE session_id LIKE 'pg-it-%'")


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    return bool(row and row[0])


def _settings_for(postgres_url: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_backend="postgres",
        database_url=postgres_url,
        memory_db_path="",
    )


def _build_counter_graph(checkpointer):
    graph = StateGraph(CounterState)

    def increment(state: CounterState) -> CounterState:
        return {"count": int(state.get("count", 0)) + 1}

    graph.add_node("increment", increment)
    graph.add_edge(START, "increment")
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.anyio
async def test_postgres_checkpointer_saves_and_restores_state(postgres_url: str):
    """官方 Postgres saver 必须能跨 saver 重建恢复 thread 状态。"""
    thread_id = f"pg-it-checkpointer-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = _build_counter_graph(await get_checkpointer(_settings_for(postgres_url)))
        await graph.ainvoke({"count": 0}, config=config)
    finally:
        await close_checkpointer()

    try:
        restored_graph = _build_counter_graph(await get_checkpointer(_settings_for(postgres_url)))
        state = await restored_graph.aget_state(config)
    finally:
        await close_checkpointer()

    assert state.values["count"] == 1


@pytest.mark.anyio
async def test_postgres_session_store_crud_and_trace_cleanup(postgres_url: str, tmp_path: Path):
    """Postgres 模式下会话元数据、checkpoint 清理和 trace 文件清理应保持一致。"""
    session_id = f"pg-it-session-{uuid4()}"
    trace_dir = tmp_path / "agent_traces"
    target_trace = resolve_trace_file(session_id, trace_dir)
    target_trace.write_text("trace", encoding="utf-8")

    try:
        with (
            pytest.MonkeyPatch.context() as monkeypatch,
        ):
            monkeypatch.setattr("app.services.session_store.settings.database_backend", "postgres")
            monkeypatch.setattr("app.services.session_store.settings.database_url", postgres_url)
            monkeypatch.setattr("app.services.session_store.resolve_trace_file", lambda _: target_trace)
            monkeypatch.setattr("app.services.session_store.uuid4", lambda: session_id)

            created = await create_chat_session("新的冒险")
            await touch_chat_session(session_id=created["id"], message="调查洞口", reply="你发现脚印。")

            with psycopg.connect(postgres_url) as conn:
                conn.execute("INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) VALUES (%s, '', '1', '{}'::jsonb, '{}'::jsonb)", (created["id"],))

            sessions = await list_chat_sessions()
            session_by_id = {item["id"]: item for item in sessions}
            assert created["id"] in session_by_id
            assert session_by_id[created["id"]]["title"] == "调查洞口"
            assert session_by_id[created["id"]]["preview"] == "你发现脚印。"

            result = await purge_chat_session_data(created["id"])

        assert result["deletedTraceFiles"] == 1
        assert not target_trace.exists()
        with psycopg.connect(postgres_url) as conn:
            assert conn.execute("SELECT COUNT(*) FROM app_chat_sessions WHERE id = %s", (created["id"],)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s", (created["id"],)).fetchone()[0] == 0
    finally:
        if target_trace.exists():
            target_trace.unlink()


def test_adventure_runtime_still_marks_pending_reward_without_postgres_side_effects():
    """数据库迁移不应改变冒险 runtime 的 pending reward 语义。"""
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="cragmaw_hideout_entrance",
                transition_kind="switch",
                confidence=0.9,
            )
        ),
    )

    assert update.adventure["active_node_id"] == "cragmaw_hideout_entrance"
    assert update.adventure["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"
    assert "player" not in update.state_update


def test_reward_claim_removes_pending_and_updates_player():
    """奖励 claim 仍只改业务状态，不依赖数据库后端。"""
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "cragmaw_hideout_entrance",
            "unlocked_node_ids": ["cragmaw_hideout_entrance"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": ["goblin_ambush_resolved", "reach_cragmaw_hideout"],
            "claimed_reward_ids": [],
            "pending_reward_grants": [
                {
                    "id": "goblin_ambush_hideout_75_xp",
                    "node_id": "cragmaw_hideout_entrance",
                    "type": "xp",
                    "amount": 75,
                    "scope": "per_player",
                    "description": "打败伏击地精并抵达克拉摩窝点后发放。",
                    "requires": ["goblin_ambush_resolved", "reach_cragmaw_hideout"],
                }
            ],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["cragmaw_hideout_entrance"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    adventure, player, result = claim_pending_reward(state, "goblin_ambush_hideout_75_xp")

    assert result["ok"] is True
    assert player["xp"] == 75
    assert adventure["claimed_reward_ids"] == ["goblin_ambush_hideout_75_xp"]
    assert adventure["pending_reward_grants"] == []
