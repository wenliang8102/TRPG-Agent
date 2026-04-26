from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.utils.agent_trace import (
    export_trace_report,
    finish_llm_trace,
    load_trace_events,
    render_trace_markdown,
    start_llm_trace,
    trace_chat_request,
    trace_chat_result,
)


def test_agent_trace_persists_full_prompt_and_response(tmp_path: Path):
    trace_chat_request(
        "session-1",
        entrypoint="stream",
        message="我攻击哥布林",
        resume_action=None,
        reaction_response=None,
        pending_before_run=None,
        trace_dir=tmp_path,
    )

    invocation_id, started_at = start_llm_trace(
        "session-1",
        mode="combat",
        phase="combat",
        system_prompt="战斗规则",
        hud_text="实时 HUD",
        messages=[HumanMessage(content="我攻击哥布林")],
        tools=[SimpleNamespace(name="attack_action", description="执行一次攻击")],
        trace_dir=tmp_path,
    )

    finish_llm_trace(
        "session-1",
        invocation_id=invocation_id,
        started_at=started_at,
        duration_ms=12.5,
        mode="combat",
        phase="combat",
        response=AIMessage(
            content="让我先为你执行攻击。",
            tool_calls=[{"name": "attack_action", "args": {"attacker_id": "player_hero"}, "id": "call_1"}],
        ),
        trace_dir=tmp_path,
    )

    trace_chat_result(
        "session-1",
        entrypoint="stream",
        reply="让我先为你执行攻击。",
        pending_action=None,
        new_message_count=1,
        trace_dir=tmp_path,
    )

    events = load_trace_events("session-1", trace_dir=tmp_path)

    assert [event["event_type"] for event in events[-4:]] == [
        "chat_request",
        "llm_invocation_started",
        "llm_invocation_completed",
        "chat_result",
    ]
    llm_start = events[-3]["payload"]
    llm_done = events[-2]["payload"]
    assert llm_start["system_prompt"] == "战斗规则"
    assert llm_start["messages"][0]["content"] == "我攻击哥布林"
    assert llm_done["response"]["tool_calls"][0]["name"] == "attack_action"


def test_export_trace_report_writes_markdown_file(tmp_path: Path):
    events = [
        {
            "session_id": "session-2",
            "event_type": "llm_invocation_started",
            "timestamp": "2026-04-26T12:00:00+08:00",
            "payload": {
                "invocation_id": "invoke-1",
                "mode": "narrative",
                "phase": "exploration",
                "system_prompt": "基础规则",
                "hud_text": "当前玩家状态",
                "messages": [{"role": "human", "content": "我推开门。"}],
                "available_tools": [{"name": "roll_dice", "description": "掷骰子"}],
            },
        },
        {
            "session_id": "session-2",
            "event_type": "llm_invocation_completed",
            "timestamp": "2026-04-26T12:00:01+08:00",
            "payload": {
                "invocation_id": "invoke-1",
                "duration_ms": 321.0,
                "response": {"role": "ai", "content": "门后有脚步声。"},
            },
        },
    ]

    markdown = render_trace_markdown("session-2", events)
    output_file = export_trace_report("session-2", events, output_path=tmp_path / "trace.md")
    exported_content = output_file.read_text(encoding="utf-8")

    assert "基础规则" in markdown
    assert "门后有脚步声。" in markdown
    assert "基础规则" in exported_content
    assert "门后有脚步声。" in exported_content
    assert "Session ID: session-2" in exported_content