"""阶段 7：用 PostgreSQL 执行一条可重复的真实状态流验证。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from langchain_core.messages import AIMessage, HumanMessage


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import settings  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE, ROUTER_NODE, TOOL_NODE  # noqa: E402
from app.memory.checkpointer import close_checkpointer, get_checkpointer  # noqa: E402
from app.prompts import get_assistant_system_prompt  # noqa: E402
from app.services.session_store import create_chat_session, list_chat_sessions, touch_chat_session  # noqa: E402
from app.services.tools import get_tool_profile  # noqa: E402
from app.services.tools.adventure_tools import (  # noqa: E402
    _advance_adventure_impl,
    _load_adventure_node_impl,
    _resolve_adventure_node_impl,
    claim_adventure_reward,
)
from app.services.tools.combat_tools import end_combat, start_combat  # noqa: E402
from app.utils.agent_trace import (  # noqa: E402
    append_trace_event,
    export_trace_report,
    load_trace_events,
    resolve_trace_file,
)
from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy  # noqa: E402


def _mask_database_url(url: str) -> str:
    if "@" not in url or ":" not in url.split("@", 1)[0]:
        return url
    prefix, suffix = url.split("@", 1)
    username = prefix.rsplit("/", 1)[-1].split(":", 1)[0]
    return f"{prefix.rsplit('/', 1)[0]}/{username}:***@{suffix}"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _kc_hashes() -> dict[str, str]:
    """记录 KC 相关稳定前缀，不触碰 prompt 或工具顺序。"""
    return {
        "narrative_prompt_hash": _hash_text(get_assistant_system_prompt(NARRATIVE_AGENT_MODE)),
        "combat_prompt_hash": _hash_text(get_assistant_system_prompt(COMBAT_AGENT_MODE)),
        "narrative_tool_order_hash": _hash_text("\n".join(tool.name for tool in get_tool_profile(NARRATIVE_AGENT_MODE))),
        "combat_tool_order_hash": _hash_text("\n".join(tool.name for tool in get_tool_profile(COMBAT_AGENT_MODE))),
    }


def _postgres_counts(database_url: str, session_id: str) -> dict[str, int]:
    with psycopg.connect(database_url) as conn:
        return {
            "app_chat_sessions": int(conn.execute("SELECT COUNT(*) FROM app_chat_sessions WHERE id = %s", (session_id,)).fetchone()[0]),
            "checkpoints": int(conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s", (session_id,)).fetchone()[0]),
            "checkpoint_blobs": int(conn.execute("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = %s", (session_id,)).fetchone()[0]),
            "checkpoint_writes": int(conn.execute("SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id = %s", (session_id,)).fetchone()[0]),
        }


def _player() -> dict[str, Any]:
    return {
        "id": "player_hero",
        "name": "温良",
        "side": "player",
        "role_class": "fighter",
        "level": 1,
        "hp": 12,
        "max_hp": 12,
        "base_ac": 16,
        "ac": 16,
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "modifiers": {"str": 3, "dex": 1, "con": 2, "int": 0, "wis": 0, "cha": 0},
        "weapons": [
            {
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_type": "slashing",
                "weapon_type": "melee",
                "reach_feet": 5,
                "attack_bonus": 5,
                "damage_bonus": 3,
            }
        ],
        "coins": {},
        "inventory": [],
        "xp": 0,
    }


def _goblin() -> dict[str, Any]:
    return {
        "id": "goblin_stage7",
        "name": "Stage7 Goblin",
        "side": "enemy",
        "hp": 7,
        "max_hp": 7,
        "base_ac": 15,
        "ac": 15,
        "speed": 30,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "modifiers": {"str": -1, "dex": 2, "con": 0, "int": 0, "wis": -1, "cha": -1},
        "attacks": [{"name": "Scimitar", "attack_bonus": 4, "damage_dice": "1d6+2", "damage_type": "slashing", "reach_feet": 5}],
        "challenge_rating": "1/4",
        "xp_value": 50,
        "action_available": True,
        "bonus_action_available": True,
        "reaction_available": True,
        "movement_left": 30,
    }


def _road_space() -> dict[str, Any]:
    return {
        "active_map_id": "stage7_road",
        "maps": {
            "stage7_road": {
                "id": "stage7_road",
                "name": "阶段7验证道路伏击点",
                "width": 80,
                "height": 60,
                "grid_size": 5,
            }
        },
        "placements": {
            "player_hero": {"unit_id": "player_hero", "map_id": "stage7_road", "position": {"x": 10, "y": 10}},
            "goblin_stage7": {"unit_id": "goblin_stage7", "map_id": "stage7_road", "position": {"x": 15, "y": 10}},
        },
    }


def _merge_state(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = {**state, **{key: value for key, value in update.items() if key != "messages"}}
    merged["messages"] = [*state.get("messages", []), *update.get("messages", [])]
    return merged


def _plain_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


async def _apply_update(graph: Any, config: dict[str, Any], state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    await graph.aupdate_state(config, update, as_node=TOOL_NODE)
    return _merge_state(state, update)


async def validate(database_url: str) -> dict[str, Any]:
    if settings.database_backend != "postgres":
        raise RuntimeError(f"TRPG_DATABASE_BACKEND must be postgres, got {settings.database_backend!r}")

    session = await create_chat_session("阶段7 PostgreSQL 验证")
    session_id = session["id"]
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": settings.graph_recursion_limit}
    graph = build_graph(await get_checkpointer(settings))

    state: dict[str, Any] = {
        "session_id": session_id,
        "phase": "exploration",
        "messages": [
            HumanMessage(content="阶段7验证：创建新 PostgreSQL 会话并加载角色。"),
            AIMessage(content="角色温良已加载，准备进入冒险模组。", tool_calls=[]),
        ],
        "player": _player(),
    }
    await graph.aupdate_state(config, state, as_node=ROUTER_NODE)
    append_trace_event(session_id, "phase7_validation_started", {"database_url": _mask_database_url(database_url)})

    # 冒险引子：加载节点、记录任务线索与出发事件，推进到地精伏击。
    state = await _apply_update(graph, config, state, _load_adventure_node_impl(None, state, "stage7-load-hook").update)
    state = await _apply_update(
        graph,
        config,
        state,
        _resolve_adventure_node_impl(
            "接受护送补给前往凡达林，并从无冬城出发。",
            ["delivery_job", "phandalin_destination"],
            ["depart_neverwinter_for_phandalin"],
            state,
            "stage7-resolve-hook",
        ).update,
    )
    state = await _apply_update(graph, config, state, _advance_adventure_impl("begin_escort_journey", state, "stage7-advance-ambush").update)
    append_trace_event(session_id, "phase7_adventure_entered", {"active_node_id": state["adventure"]["active_node_id"]})

    # 进入并完成一次战斗，随后在地精伏击节点记录事件与线索。
    state["scene_units"] = {"goblin_stage7": _goblin()}
    state["space"] = _road_space()
    state = await _apply_update(graph, config, state, {"scene_units": state["scene_units"], "space": state["space"]})
    state = await _apply_update(
        graph,
        config,
        state,
        start_combat.func(combatant_ids=["goblin_stage7"], surprised_ids=["player_hero"], state=state, tool_call_id="stage7-start-combat").update,
    )
    entered_combat = state.get("phase") == "combat" and bool(state.get("combat"))
    state = await _apply_update(
        graph,
        config,
        state,
        end_combat.func(defeated_unit_ids=["goblin_stage7"], departed_unit_ids=[], state=state, tool_call_id="stage7-end-combat").update,
    )
    combat_completed = state.get("phase") == "exploration" and state.get("combat") is None
    state = await _apply_update(
        graph,
        config,
        state,
        _resolve_adventure_node_impl(
            "地精伏击被击退，并发现通往克拉摩窝点的踪迹。",
            ["goblin_trail"],
            ["goblin_ambush_resolved"],
            state,
            "stage7-resolve-ambush",
        ).update,
    )
    append_trace_event(
        session_id,
        "phase7_combat_completed",
        {"entered_combat": entered_combat, "combat_completed": combat_completed, "events": state["adventure"]["completed_event_ids"]},
    )

    # 推进到踪迹节点和克拉摩窝点入口，触发 pending reward。
    state = await _apply_update(graph, config, state, _advance_adventure_impl("investigate_goblin_trail", state, "stage7-advance-trail").update)
    state = await _apply_update(graph, config, state, _advance_adventure_impl("follow_trail_to_hideout", state, "stage7-advance-hideout").update)
    pending_reward_ids = [item["id"] for item in state["adventure"].get("pending_reward_grants", [])]
    append_trace_event(session_id, "phase7_pending_rewards", {"pending_reward_ids": pending_reward_ids})

    # 领取剧情奖励，验证角色状态被写回。
    reward_id = "goblin_ambush_hideout_75_xp"
    state = await _apply_update(
        graph,
        config,
        state,
        claim_adventure_reward.func(reward_id=reward_id, state=state, tool_call_id="stage7-claim-xp").update,
    )
    claimed_reward_ids = state["adventure"].get("claimed_reward_ids", [])
    player = state["player"]
    await touch_chat_session(session_id=session_id, message="阶段7验证完成", reply="PostgreSQL 阶段7验证状态流完成。")
    append_trace_event(
        session_id,
        "phase7_reward_claimed",
        {"claimed_reward_ids": claimed_reward_ids, "player_xp": player.get("xp"), "coins": player.get("coins"), "inventory": player.get("inventory")},
    )

    # 模拟后端重启：关闭 checkpointer，再重新创建 graph 读取同一个 thread。
    await close_checkpointer()
    restored_graph = build_graph(await get_checkpointer(settings))
    restored_state = await restored_graph.aget_state(config)
    restored_values = restored_state.values
    restored_adventure = _plain_dict(restored_values.get("adventure"))
    restored_player = _plain_dict(restored_values.get("player"))
    await close_checkpointer()

    listed_sessions = await list_chat_sessions()
    trace_events = load_trace_events(session_id)
    trace_file = resolve_trace_file(session_id)
    export_path = export_trace_report(session_id, trace_events)

    report = {
        "session_id": session_id,
        "database_url": _mask_database_url(database_url),
        "created_session": bool(session.get("id")),
        "history_listed": any(item["id"] == session_id for item in listed_sessions),
        "adventure_active_node": state["adventure"]["active_node_id"],
        "known_clue_ids": state["adventure"]["known_clue_ids"],
        "completed_event_ids": state["adventure"]["completed_event_ids"],
        "entered_combat": entered_combat,
        "combat_completed": combat_completed,
        "pending_reward_ids_before_claim": pending_reward_ids,
        "claimed_reward_ids": claimed_reward_ids,
        "player_xp": player.get("xp"),
        "player_coins": player.get("coins", {}),
        "player_inventory": player.get("inventory", []),
        "checkpoint_restored": restored_adventure.get("active_node_id") == state["adventure"]["active_node_id"]
        and restored_player.get("xp") == player.get("xp"),
        "restored_active_node": restored_adventure.get("active_node_id"),
        "restored_player_xp": restored_player.get("xp"),
        "trace_file": str(trace_file),
        "trace_exists": trace_file.exists(),
        "trace_event_count": len(trace_events),
        "trace_export": str(export_path),
        "trace_export_exists": export_path.exists(),
        "postgres_rows": _postgres_counts(database_url, session_id),
        "kc": _kc_hashes(),
    }

    required = {
        "created_session": report["created_session"],
        "history_listed": report["history_listed"],
        "goblin_trail": "goblin_trail" in report["known_clue_ids"],
        "goblin_ambush_resolved": "goblin_ambush_resolved" in report["completed_event_ids"],
        "reach_cragmaw_hideout": "reach_cragmaw_hideout" in report["completed_event_ids"],
        "entered_combat": report["entered_combat"],
        "combat_completed": report["combat_completed"],
        "pending_reward": reward_id in report["pending_reward_ids_before_claim"],
        "claimed_reward": reward_id in report["claimed_reward_ids"],
        "player_xp": int(report["player_xp"] or 0) >= 75,
        "checkpoint_restored": report["checkpoint_restored"],
        "trace_exists": report["trace_exists"],
        "trace_export_exists": report["trace_export_exists"],
        "postgres_checkpoints": report["postgres_rows"]["checkpoints"] > 0,
    }
    report["checks"] = required
    report["ok"] = all(required.values())
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("TRPG_DATABASE_URL") or settings.database_url)
    parser.add_argument("--json", action="store_true", help="只输出 JSON 报告。")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    if not args.database_url:
        print("TRPG_DATABASE_URL or --database-url is required.", file=sys.stderr)
        return 2
    report = await validate(args.database_url)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main() -> int:
    configure_windows_selector_event_loop_policy()
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
