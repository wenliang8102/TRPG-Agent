from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE
from app.graph import nodes
from app.memory.context_assembler import OptionalContextBlock
from app.rag.auto_rule_evidence import AutoRuleEvidenceEvaluation


class _FakeLLMService:
    def __init__(self) -> None:
        self.messages = []
        self.tools = []

    def invoke_with_tools(self, *, messages, tools, system_prompt, mode):
        self.messages = messages
        self.tools = tools
        return AIMessage(content="继续。")


def test_optional_rule_rag_is_disabled_by_default(monkeypatch):
    fake_llm = _FakeLLMService()
    monkeypatch.setattr(nodes.settings, "agent_trace_enabled", False)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_enabled", False)
    monkeypatch.setattr(nodes, "_get_llm_service", lambda: fake_llm)
    monkeypatch.setattr(nodes, "get_tool_profile", lambda mode: [])
    monkeypatch.setattr(nodes, "get_assistant_system_prompt", lambda mode: "基础规则")

    def _unexpected_auto_rag(*args, **kwargs):
        raise AssertionError("默认关闭时不应启动机会型 RAG")

    monkeypatch.setattr(nodes, "evaluate_auto_rule_evidence", _unexpected_auto_rag)

    result = nodes.assistant_node(
        {
            "session_id": "session-disabled",
            "messages": [HumanMessage(content="树后有掩护吗？")],
        }
    )

    runtime_message = fake_llm.messages[-1]
    assert result["output"] == "继续。"
    assert "[机会型上下文]" not in runtime_message.content


def test_optional_rule_rag_appends_evidence_when_enabled(monkeypatch):
    fake_llm = _FakeLLMService()
    monkeypatch.setattr(nodes.settings, "agent_trace_enabled", False)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_enabled", True)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_timeout_ms", 1000)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_top_k", 6)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_min_score", 0.5)
    monkeypatch.setattr(nodes, "_get_llm_service", lambda: fake_llm)
    monkeypatch.setattr(nodes, "get_tool_profile", lambda mode: [])
    monkeypatch.setattr(nodes, "get_assistant_system_prompt", lambda mode: "基础规则")

    def _fake_auto_rag(*args, **kwargs):
        return AutoRuleEvidenceEvaluation(
            evidence=OptionalContextBlock(
                title="规则证据候选",
                source="auto_rule_rag",
                content="半身掩护提供 AC 和敏捷豁免加值。",
            ),
            injected=True,
            reason="injected",
        )

    monkeypatch.setattr(nodes, "evaluate_auto_rule_evidence", _fake_auto_rag)

    nodes.assistant_node(
        {
            "session_id": "session-enabled",
            "messages": [HumanMessage(content="树后有掩护吗？")],
        }
    )

    runtime_message = fake_llm.messages[-1]
    assert "[机会型上下文]" in runtime_message.content
    assert "[规则证据候选 | source=auto_rule_rag]" in runtime_message.content
    assert "半身掩护提供 AC 和敏捷豁免加值。" in runtime_message.content


def test_optional_rule_rag_skips_tool_followup_invocation(monkeypatch):
    fake_llm = _FakeLLMService()
    monkeypatch.setattr(nodes.settings, "agent_trace_enabled", False)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_enabled", True)
    monkeypatch.setattr(nodes, "_get_llm_service", lambda: fake_llm)
    monkeypatch.setattr(nodes, "get_tool_profile", lambda mode: [])
    monkeypatch.setattr(nodes, "get_assistant_system_prompt", lambda mode: "基础规则")

    def _unexpected_auto_rag(*args, **kwargs):
        raise AssertionError("工具返回后的二次 assistant 不应重复启动机会型 RAG")

    monkeypatch.setattr(nodes, "evaluate_auto_rule_evidence", _unexpected_auto_rag)

    nodes.assistant_node(
        {
            "session_id": "session-tool-followup",
            "messages": [
                HumanMessage(content="躲在树后能给我什么掩护？"),
                AIMessage(content="", tool_calls=[{"name": "consult_rules_handbook", "args": {}, "id": "call_1"}]),
                ToolMessage(content="掩护规则结果", tool_call_id="call_1", name="consult_rules_handbook"),
            ],
        }
    )

    runtime_message = fake_llm.messages[-1]
    assert "[机会型上下文]" not in runtime_message.content


def test_combat_assistant_uses_official_tool_profile_without_node_filter(monkeypatch):
    fake_llm = _FakeLLMService()
    monkeypatch.setattr(nodes.settings, "agent_trace_enabled", False)
    monkeypatch.setattr(nodes.settings, "auto_rule_rag_enabled", False)
    monkeypatch.setattr(nodes, "_get_llm_service", lambda: fake_llm)
    monkeypatch.setattr(nodes, "get_assistant_system_prompt", lambda mode: "战斗规则")
    monkeypatch.setattr(
        nodes,
        "get_tool_profile",
        lambda mode: [
            SimpleNamespace(name="delegate_combat_turn"),
            SimpleNamespace(name="consult_rules_handbook"),
            SimpleNamespace(name="attack"),
            SimpleNamespace(name="cast_spell"),
            ],
    )

    nodes.combat_assistant_node(
        {
            "player": {"id": "player_hero"},
            "combat": {
                "current_actor_id": "goblin_1",
                "participants": {
                    "goblin_1": {
                        "id": "goblin_1",
                        "side": "enemy",
                        "hp": 7,
                    }
                },
            },
        }
    )

    assert [tool.name for tool in fake_llm.tools] == [
        "delegate_combat_turn",
        "consult_rules_handbook",
        "attack",
        "cast_spell",
    ]
