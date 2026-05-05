import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.memory.context_assembler import ContextAssembler, summarize_tool_message


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
        self.assertNotIn("[可按需加载的技能]", assembled.system_prompt)
        self.assertNotIn("character_state_management", assembled.system_prompt)
        self.assertIn("[扩展上下文]", assembled.system_prompt)
        self.assertIn("外部上下文:narrative", assembled.system_prompt)
        self.assertIn("状态快照", assembled.hud_text)
        self.assertIsInstance(assembled.model_input_messages[-2], SystemMessage)
        self.assertIn("<runtime_state", assembled.model_input_messages[-2].content)
        self.assertIn('source="hud"', assembled.model_input_messages[-2].content)
        self.assertIn('visibility="model_only"', assembled.model_input_messages[-2].content)
        self.assertNotIn("复述", assembled.model_input_messages[-2].content)
        self.assertNotIn("解释", assembled.model_input_messages[-2].content)
        self.assertIn("状态快照", assembled.model_input_messages[-2].content)
        self.assertEqual("我看看四周。", assembled.model_input_messages[-1].content)
        self.assertIn("[当前平面空间]", assembled.hud_text)
        self.assertIn("当前没有平面地图", assembled.hud_text)

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

    def test_hud_preserves_trailing_tool_exchange_for_followup_call(self):
        assembler = ContextAssembler()
        state = {
            "messages": [
                HumanMessage(content="continue"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "attack_action", "args": {"attacker_id": "goblin_1"}, "id": "call_1"}],
                ),
                ToolMessage(content="attack resolved", tool_call_id="call_1", name="attack_action"),
            ]
        }

        assembled = assembler.assemble(state, COMBAT_AGENT_MODE, base_system_prompt="combat rules")

        self.assertIsInstance(assembled.model_input_messages[-3], SystemMessage)
        self.assertIsInstance(assembled.model_input_messages[-2], AIMessage)
        self.assertIsInstance(assembled.model_input_messages[-1], ToolMessage)
        self.assertIn("<runtime_state", assembled.model_input_messages[-3].content)
        self.assertIn("[工具:attack_action]", assembled.model_input_messages[-1].content)

    def test_archived_combat_expands_start_to_triggering_ai_tool_call(self):
        assembler = ContextAssembler()
        state = {
            "messages": [
                HumanMessage(content="狼冲出来了。"),
                AIMessage(content="", tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["wolf_1"]}, "id": "call_start"}]),
                ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_start", name="start_combat"),
                AIMessage(content="", tool_calls=[{"name": "end_combat", "args": {}, "id": "call_end"}]),
                ToolMessage(content="共进行了 1 回合。 倒下: Wolf", tool_call_id="call_end", name="end_combat"),
                HumanMessage(content="我检查四周。"),
            ],
            "combat_archives": [
                {
                    "summary": "法师击倒了野狼，战斗结束。",
                    "start_index": 2,
                    "end_index": 4,
                }
            ],
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        dangling_tool_call_messages = [
            message for message in assembled.model_input_messages
            if isinstance(message, AIMessage) and message.tool_calls
        ]
        self.assertEqual([], dangling_tool_call_messages)
        self.assertTrue(any(
            isinstance(message, HumanMessage)
            and isinstance(message.content, str)
            and message.content.startswith("[系统:战斗归档]")
            for message in assembled.model_input_messages
        ))

    def test_projection_strips_legacy_dangling_tool_call(self):
        assembler = ContextAssembler()
        state = {
            "messages": [
                HumanMessage(content="结束战斗。"),
                AIMessage(content="战斗结束。", tool_calls=[{"name": "end_combat", "args": {}, "id": "call_end"}]),
                AIMessage(content="你确认周围暂时安全。", tool_calls=[]),
            ],
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        ai_messages = [message for message in assembled.model_input_messages if isinstance(message, AIMessage)]
        self.assertTrue(any(message.content == "战斗结束。" for message in ai_messages))
        self.assertFalse(any(message.tool_calls for message in ai_messages))

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

    def test_combat_context_lists_monster_actions(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="继续战斗。")],
            "combat": {
                "round": 1,
                "current_actor_id": "goblin_1",
                "initiative_order": ["goblin_1"],
                "participants": {
                    "goblin_1": {
                        "name": "Goblin",
                        "side": "enemy",
                        "hp": 7,
                        "max_hp": 7,
                        "ac": 15,
                        "actions": [
                            {"id": "scimitar", "name": "Scimitar", "kind": "attack"},
                            {"id": "nimble_escape", "name": "Nimble Escape", "kind": "bonus_action"},
                        ],
                        "attacks": [{"name": "Scimitar"}],
                    }
                },
            },
        }

        assembled = assembler.assemble(state, COMBAT_AGENT_MODE, base_system_prompt="战斗规则")

        self.assertIn("actions:Scimitar(scimitar, attack)", assembled.system_prompt)
        self.assertIn("actions=[Scimitar(scimitar, attack)", assembled.hud_text)
        self.assertNotIn("attacks:[", assembled.system_prompt)

    def test_hud_includes_planar_space_summary(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我观察站位。")],
            "space": {
                "active_map_id": "map_hall",
                "maps": {
                    "map_hall": {
                        "id": "map_hall",
                        "name": "大厅",
                        "width": 80,
                        "height": 60,
                        "grid_size": 5,
                    }
                },
                "placements": {
                    "goblin_1": {
                        "unit_id": "goblin_1",
                        "map_id": "map_hall",
                        "position": {"x": 10, "y": 15},
                        "facing_deg": 90,
                    }
                },
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[当前平面空间]", assembled.hud_text)
        self.assertIn("大厅", assembled.hud_text)
        self.assertIn("当前地图单位坐标", assembled.hud_text)
        self.assertIn("goblin_1: (10, 15)", assembled.hud_text)

    def test_inspect_unit_tool_projection_keeps_structured_character_facts(self):
        tool_message = ToolMessage(
            content=(
                "[玩家角色] player_hero 完整信息:\n"
                '{"id":"player_hero","name":"英雄","role_class":"法师","level":2,'
                '"hp":8,"max_hp":12,"resources":{"spell_slot_lv1":1},'
                '"conditions":[{"id":"arcane_ward","extra":{"ward_hp":7}}],'
                '"known_spells":["magic_missile","shield"],'
                '"long_notes":"这段冗长说明不应挤掉关键结构字段"}'
            ),
            tool_call_id="call_1",
            name="inspect_unit",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("[工具:inspect_unit]", projected)
        self.assertIn("spell_slot_lv1", projected)
        self.assertIn("magic_missile", projected)
        self.assertIn("arcane_ward", projected)
        self.assertIn("long_notes", projected)


if __name__ == "__main__":
    unittest.main()
