import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.memory.context_assembler import ContextAssembler


class _StaticContextProvider:
    def get_context_blocks(self, *, state, mode):
        return [f"外部上下文:{mode}"]


class ContextAssemblerTests(unittest.TestCase):
    def test_assemble_includes_summary_external_context_and_hud(self):
        assembler = ContextAssembler(external_context_provider=_StaticContextProvider())
        state = {
            "conversation_summary": "玩家刚踏入地牢入口。",
            "messages": [HumanMessage(content="我看看四周。")],
            "player": {"id": "player_hero", "name": "英雄", "role_class": "法师"},
            "scene_units": {"goblin_1": {"name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7}},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("玩家刚踏入地牢入口。", assembled.system_prompt)
        self.assertIn("[扩展上下文]", assembled.system_prompt)
        self.assertIn("外部上下文:narrative", assembled.system_prompt)
        self.assertIn("实时系统监控窗", assembled.hud_text)
        self.assertIn("实时系统监控窗", assembled.model_input_messages[-1].content)

    def test_episodic_context_precedes_summary_fallback(self):
        assembler = ContextAssembler()
        state = {
            "conversation_summary": "这是旧摘要，不应在有 episodic context 时继续注入。",
            "episodic_context": ["玩家刚进入地牢。", "上一轮已经听见祭坛后的脚步声。"],
            "messages": [HumanMessage(content="我推开门。")],
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[近期情节记忆]", assembled.system_prompt)
        self.assertIn("玩家刚进入地牢。", assembled.system_prompt)
        self.assertNotIn("这是旧摘要", assembled.system_prompt)

    def test_assemble_trims_without_starting_from_tool_message(self):
        assembler = ContextAssembler()
        messages = [HumanMessage(content="旧消息")]
        messages.append(AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {}, "id": "call_1"}]))
        messages.append(ToolMessage(content="Goblin 使用弯刀攻击。\n英雄 HP: 18 -> 13", tool_call_id="call_1", name="attack_action"))
        messages.extend(HumanMessage(content=f"消息 {index}") for index in range(49))

        assembled = assembler.assemble({"messages": messages}, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIsInstance(assembled.model_input_messages[0], AIMessage)
        tool_messages = [message for message in assembled.model_input_messages if isinstance(message, ToolMessage)]
        self.assertEqual(1, len(tool_messages))
        self.assertIn("[工具:attack_action]", tool_messages[0].content)

    def test_assemble_adds_combat_brief_and_turn_directive(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我攻击哥布林")],
            "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 12, "max_hp": 12, "ac": 14, "attacks": [{"name": "法杖"}]},
            "combat": {
                "round": 2,
                "current_actor_id": "goblin_1",
                "initiative_order": ["goblin_1", "player_hero"],
                "participants": {
                    "goblin_1": {"name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7, "ac": 15, "attacks": [{"name": "Scimitar"}]}
                },
            },
        }

        assembled = assembler.assemble(state, COMBAT_AGENT_MODE, base_system_prompt="战斗规则")

        self.assertIn("[战斗简报]", assembled.system_prompt)
        self.assertIn("[当前回合指令]", assembled.system_prompt)
        self.assertIn("Goblin", assembled.system_prompt)


if __name__ == "__main__":
    unittest.main()