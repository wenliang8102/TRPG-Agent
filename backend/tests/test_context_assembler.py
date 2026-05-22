import json
import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.memory.context_assembler import (
    ADVENTURE_NODE_FRAME_MESSAGE_PREFIX,
    AdventureNodeRetrievalContextProvider,
    ContextAssembler,
    OptionalContextBlock,
    build_runtime_state_message,
    summarize_tool_message,
    trim_model_messages,
)


class _StaticContextProvider:
    def get_context_blocks(self, *, state, mode):
        return [f"外部上下文:{mode}"]


class ContextAssemblerTests(unittest.TestCase):
    def test_assemble_delegates_to_required_context_without_behavior_change(self):
        assembler = ContextAssembler(external_context_provider=_StaticContextProvider())
        state = {
            "messages": [HumanMessage(content="我查看前方。")],
            "player": {"id": "player_hero", "name": "英雄"},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")
        required = assembler.assemble_required(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertEqual(required.system_prompt, assembled.system_prompt)
        self.assertEqual(required.hud_text, assembled.hud_text)
        self.assertEqual(required.runtime_state_text, assembled.runtime_state_text)
        self.assertEqual(required.model_input_messages, assembled.model_input_messages)

    def test_required_context_keeps_adventure_node_facts_out_of_optional_branch(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我想去凡达林。")],
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["lost_mine_start", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": ["goblin_trail"],
                "completed_event_ids": ["goblin_ambush_resolved"],
                "pending_exit_option_ids": [],
            },
        }

        required = assembler.assemble_required(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[扩展上下文]", required.runtime_state_text)
        self.assertIn("[冒险节点事实]", required.runtime_state_text)
        self.assertIn("当前节点: 地精伏击 [ID:goblin_ambush]", required.runtime_state_text)
        self.assertNotIn("[机会型上下文]", required.runtime_state_text)

    def test_optional_runtime_context_is_explicit_and_ephemeral(self):
        assembler = ContextAssembler()
        state = {
            "messages": [
                HumanMessage(content="[系统:运行状态帧]\n[机会型上下文]\n旧规则证据不应残留。"),
                HumanMessage(content="我躲到石柱后。"),
            ],
        }

        required = assembler.assemble_required(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")
        assembled = assembler.append_optional_runtime_context(
            required,
            [
                OptionalContextBlock(
                    title="规则证据候选",
                    source="auto_rule_rag",
                    content="半身掩护通常提供 AC 和敏捷豁免加值。",
                )
            ],
        )

        self.assertNotIn("[机会型上下文]", required.runtime_state_text)
        self.assertIn("[机会型上下文]", assembled.runtime_state_text)
        self.assertIn("[规则证据候选 | source=auto_rule_rag]", assembled.runtime_state_text)
        self.assertIn("半身掩护通常提供 AC 和敏捷豁免加值。", assembled.runtime_state_text)
        self.assertEqual(required.model_input_messages, assembled.model_input_messages)
        projected_text = "\n".join(str(message.content) for message in assembled.model_input_messages)
        self.assertNotIn("旧规则证据不应残留", projected_text)

    def test_empty_optional_runtime_context_keeps_required_context_unchanged(self):
        assembler = ContextAssembler()
        required = assembler.assemble_required(
            {"messages": [HumanMessage(content="继续。")]},
            NARRATIVE_AGENT_MODE,
            base_system_prompt="基础规则",
        )

        assembled = assembler.append_optional_runtime_context(
            required,
            [OptionalContextBlock(title="规则证据候选", content="   ", source="auto_rule_rag")],
        )

        self.assertEqual(required.runtime_state_text, assembled.runtime_state_text)
        self.assertEqual(required.model_input_messages, assembled.model_input_messages)

    def test_assemble_includes_external_context_and_hud_without_hot_summary(self):
        assembler = ContextAssembler(external_context_provider=_StaticContextProvider())
        state = {
            "conversation_summary": "玩家刚踏入地牢入口。",
            "messages": [HumanMessage(content="我看看四周。")],
            "player": {"id": "player_hero", "name": "英雄", "role_class": "法师"},
            "scene_units": {"goblin_1": {"name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7}},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertEqual("基础规则", assembled.system_prompt)
        self.assertNotIn("[可按需加载的技能]", assembled.system_prompt)
        self.assertNotIn("character_state_management", assembled.system_prompt)
        self.assertNotIn("玩家刚踏入地牢入口。", assembled.runtime_state_text)
        self.assertIn("[扩展上下文]", assembled.runtime_state_text)
        self.assertIn("外部上下文:narrative", assembled.runtime_state_text)
        self.assertIn("状态快照", assembled.hud_text)
        self.assertEqual("我看看四周。", assembled.model_input_messages[-1].content)
        runtime_message = build_runtime_state_message(assembled.runtime_state_text)
        self.assertIn("<runtime_state_frame", runtime_message.content)
        self.assertIn("[系统:运行状态帧]", runtime_message.content)
        self.assertIn('source="state"', runtime_message.content)
        self.assertIn('visibility="model_only"', runtime_message.content)
        self.assertNotIn("复述", runtime_message.content)
        self.assertNotIn("解释", runtime_message.content)
        self.assertNotIn("=== 状态快照 ===", runtime_message.content)
        self.assertIn("[当前平面空间]", assembled.hud_text)
        self.assertIn("当前没有平面地图", assembled.hud_text)

    def test_episodic_context_is_not_injected_into_runtime_state(self):
        assembler = ContextAssembler()
        state = {
            "conversation_summary": "这是旧摘要，不应在有 episodic context 时继续注入。",
            "episodic_context": ["玩家刚进入地牢。", "上一轮已经听见祭坛后的脚步声。"],
            "messages": [HumanMessage(content="我推开门。")],
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertNotIn("[近期情节记忆]", assembled.runtime_state_text)
        self.assertNotIn("玩家刚进入地牢。", assembled.runtime_state_text)
        self.assertNotIn("这是旧摘要", assembled.runtime_state_text)

    def test_opening_prompt_requests_fighter_companion_when_missing(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="开始冒险。")],
            "player": {"id": "player_hero", "name": "英雄", "side": "player"},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[开局友方准则]", assembled.runtime_state_text)
        self.assertIn("fighter_companion", assembled.runtime_state_text)
        self.assertIn("格林", assembled.runtime_state_text)
        self.assertIn("不是修达", assembled.runtime_state_text)

    def test_opening_prompt_skips_when_ally_exists(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="开始冒险。")],
            "player": {"id": "player_hero", "name": "英雄", "side": "player"},
            "scene_units": {"fighter_companion": {"id": "fighter_companion", "name": "格林", "side": "ally"}},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertNotIn("[开局友方准则]", assembled.runtime_state_text)

    def test_narrative_runtime_state_lists_player_and_scene_unit_spells(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我准备探索。")],
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "known_clue_ids": ["goblin_trail"],
                "completed_event_ids": ["goblin_ambush_resolved"],
                "pending_exit_option_ids": ["investigate_goblin_trail"],
            },
            "player": {
                "id": "player_hero",
                "name": "英雄",
                "side": "player",
                "hp": 10,
                "max_hp": 12,
                "resources": {"spell_slot_lv1": 1},
                "known_spells": ["magic_missile", "shield"],
                "known_cantrips": ["fire_bolt"],
            },
            "scene_units": {
                "apprentice_wizard": {
                    "id": "apprentice_wizard",
                    "name": "伊莲",
                    "side": "ally",
                    "hp": 8,
                    "max_hp": 10,
                    "ac": 12,
                    "resources": {"spell_slot_lv1": 2},
                    "resource_caps": {"spell_slot_lv1": 3},
                    "known_spells": ["magic_missile"],
                    "known_cantrips": ["ray_of_frost"],
                    "class_features": ["action_surge", "arcane_recovery"],
                    "actions": [{"id": "dagger", "name": "Dagger", "kind": "attack"}],
                },
                "goblin_1": {"id": "goblin_1", "name": "Goblin", "side": "enemy"},
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("magic:[spells=magic_missile,shield; cantrips=fire_bolt]", assembled.runtime_state_text)
        self.assertIn("冒险节点: lost_mine / goblin_ambush", assembled.runtime_state_text)
        self.assertIn("已知线索: ['goblin_trail']", assembled.runtime_state_text)
        self.assertIn("已完成事件: ['goblin_ambush_resolved']", assembled.runtime_state_text)
        self.assertIn("待确认出口: ['investigate_goblin_trail']", assembled.runtime_state_text)
        self.assertIn("可用法术单位: 伊莲[ID:apprentice_wizard", assembled.runtime_state_text)
        self.assertIn("spells=magic_missile; cantrips=ray_of_frost", assembled.runtime_state_text)
        self.assertIn("可见友方: 伊莲[ID:apprentice_wizard", assembled.runtime_state_text)
        self.assertIn("HP:8/10", assembled.runtime_state_text)
        self.assertIn("resources:spell_slot_lv1=2/3", assembled.runtime_state_text)
        self.assertIn("class_actions:action_surge", assembled.runtime_state_text)
        self.assertIn("arcane_recovery", assembled.runtime_state_text)
        self.assertIn("actions:Dagger(dagger, attack)", assembled.runtime_state_text)

    def test_narrative_runtime_state_includes_current_node_facts_and_related_hits(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我想去凡达林。")],
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["lost_mine_start", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": ["goblin_trail"],
                "completed_event_ids": ["goblin_ambush_resolved"],
                "pending_exit_option_ids": [],
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[扩展上下文]", assembled.runtime_state_text)
        self.assertIn("[冒险节点事实]", assembled.runtime_state_text)
        self.assertIn("当前节点: 地精伏击 [ID:goblin_ambush]", assembled.runtime_state_text)
        self.assertIn("investigate_goblin_trail: 搜索伏击点并追踪地精 -> goblin_trail_to_cragmaw_hideout", assembled.runtime_state_text)
        self.assertIn("go_to_phandalin_first: 先去凡达林 -> phandalin", assembled.runtime_state_text)
        self.assertIn("[冒险节点检索]", assembled.runtime_state_text)
        self.assertIn("最近意图: 我想去凡达林。", assembled.runtime_state_text)
        self.assertRegex(assembled.runtime_state_text, r"phandalin(_arrival)?")

    def test_narrative_runtime_state_caps_known_clue_window(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="我继续追查线索。")],
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "known_clue_ids": [f"clue_{index}" for index in range(10)],
                "completed_event_ids": ["goblin_ambush_resolved"],
                "pending_exit_option_ids": [],
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("已知线索:", assembled.runtime_state_text)
        self.assertIn("另有 4 条", assembled.runtime_state_text)
        self.assertNotIn("clue_0", assembled.runtime_state_text)

    def test_hud_marks_ally_scene_unit_id_as_start_combat_input(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="开战。")],
            "scene_units": {
                "fighter_companion": {
                    "id": "fighter_companion",
                    "name": "格林",
                    "side": "ally",
                    "hp": 12,
                    "max_hp": 12,
                },
                "goblin_1": {"id": "goblin_1", "name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7},
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("start_combat 的非玩家参战 ID 来源", assembled.hud_text)
        self.assertIn("敌人和友方都必须显式列入 combatant_ids", assembled.hud_text)
        self.assertIn('start_combat.combatant_ids 可直接使用 "fighter_companion"', assembled.hud_text)
        self.assertIn('start_combat.combatant_ids 可直接使用 "goblin_1"', assembled.hud_text)

    def test_narrative_runtime_state_periodically_anchors_adventure_node(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content=f"探索 {index}") for index in range(4)],
            "adventure": {"module_id": "lost_mine", "active_node_id": "goblin_ambush"},
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[冒险节点校准]", assembled.runtime_state_text)
        self.assertIn("goblin_ambush", assembled.runtime_state_text)
        self.assertIn("优先沿当前节点给出可执行后果、线索或下一步压力", assembled.runtime_state_text)
        self.assertIn("路径回溯", assembled.runtime_state_text)
        self.assertIn("待回访节点", assembled.runtime_state_text)
        self.assertIn("不要顺着即兴逻辑游离到未建立的地点", assembled.runtime_state_text)

    def test_adventure_node_anchor_skips_combat_mode(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content=f"继续 {index}") for index in range(4)],
            "adventure": {"module_id": "lost_mine", "active_node_id": "goblin_ambush"},
            "combat": {
                "round": 1,
                "current_actor_id": "goblin_1",
                "initiative_order": ["goblin_1"],
                "participants": {
                    "goblin_1": {"name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7, "ac": 15}
                },
            },
        }

        assembled = assembler.assemble(state, COMBAT_AGENT_MODE, base_system_prompt="战斗规则")

        self.assertNotIn("[冒险节点校准]", assembled.runtime_state_text)

    def test_narrative_runtime_state_keeps_guardrail_internal(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="继续")],
            "adventure": {"module_id": "lost_mine", "active_node_id": "goblin_ambush"},
            "adventure_guardrail_warning": {
                "node_id": "goblin_ambush",
                "warning": "上一轮回复越过了当前节点。",
                "unsupported_claims": ["冈德伦已被救出", "红标帮在酒馆埋伏"],
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[内部冒险校准]", assembled.runtime_state_text)
        self.assertIn("不得向玩家复述", assembled.runtime_state_text)
        self.assertNotIn("冈德伦已被救出", assembled.runtime_state_text)

    def test_narrative_runtime_state_requires_load_node_after_runtime_advance(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="继续")],
            "adventure": {"module_id": "lost_mine", "active_node_id": "goblin_ambush"},
            "adventure_runtime_directive": {
                "kind": "node_advanced",
                "node_id": "goblin_ambush",
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertIn("[内部冒险流程]", assembled.runtime_state_text)
        self.assertIn("后台已将冒险书签推进到 goblin_ambush", assembled.runtime_state_text)
        self.assertIn("本轮第一步先按当前节点事实重整叙事", assembled.runtime_state_text)

    def test_assemble_keeps_full_history_under_large_context_budget(self):
        assembler = ContextAssembler()
        messages = [HumanMessage(content="旧消息")]
        messages.append(AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {}, "id": "call_1"}]))
        messages.append(ToolMessage(content="Goblin 使用弯刀攻击。\n英雄 HP: 18 -> 13", tool_call_id="call_1", name="attack_action"))
        messages.extend(HumanMessage(content=f"消息 {index}") for index in range(39))

        assembled = assembler.assemble({"messages": messages}, NARRATIVE_AGENT_MODE, base_system_prompt="基础规则")

        self.assertEqual("旧消息", assembled.model_input_messages[0].content)
        tool_messages = [message for message in assembled.model_input_messages if isinstance(message, ToolMessage)]
        self.assertEqual(1, len(tool_messages))
        self.assertIn("[工具:attack_action]", tool_messages[0].content)

    def test_budget_trim_never_starts_from_tool_message(self):
        messages = [HumanMessage(content="旧消息")]
        messages.append(AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {}, "id": "call_1"}]))
        messages.append(ToolMessage(content="Goblin 使用弯刀攻击。\n英雄 HP: 18 -> 13", tool_call_id="call_1", name="attack_action"))
        messages.extend(HumanMessage(content="x" * 50_000) for _ in range(40))

        trimmed = trim_model_messages(messages, NARRATIVE_AGENT_MODE)

        self.assertFalse(isinstance(trimmed[0], ToolMessage))

    def test_budget_trim_inserts_thick_context_archive(self):
        messages = [HumanMessage(content=f"重要人设线索 {index}: 巴伦害怕深水但信守承诺。" + ("x" * 50_000)) for index in range(40)]

        trimmed = trim_model_messages(messages, NARRATIVE_AGENT_MODE)

        self.assertIsInstance(trimmed[0], HumanMessage)
        self.assertIn("[系统:上下文预算归档]", trimmed[0].content)
        self.assertIn("重要人设线索", trimmed[0].content)
        self.assertIn("巴伦害怕深水但信守承诺", trimmed[0].content)

    def test_model_input_preserves_trailing_tool_exchange_without_ephemeral_runtime_state(self):
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

        self.assertIsInstance(assembled.model_input_messages[-2], AIMessage)
        self.assertIsInstance(assembled.model_input_messages[-1], ToolMessage)
        self.assertIn("[工具:attack_action]", assembled.model_input_messages[-1].content)

    def test_model_input_filters_adventure_node_frames_from_main_agent(self):
        assembler = ContextAssembler()
        state = {
            "messages": [
                HumanMessage(content="旧输入"),
                HumanMessage(content=f"{ADVENTURE_NODE_FRAME_MESSAGE_PREFIX}\n{{\"node_id\":\"goblin_ambush\"}}"),
                HumanMessage(content="继续"),
            ]
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="rules")

        contents = [str(message.content) for message in assembled.model_input_messages]
        self.assertEqual(["旧输入", "继续"], contents)

    def test_adventure_node_tool_projection_keeps_structured_material(self):
        tool_message = ToolMessage(
            content=json.dumps(
                {
                    "node": {
                        "id": "goblin_ambush",
                        "title": "地精伏击",
                        "kind": "encounter",
                        "source_pages": [6, 7],
                        "source_excerpt": "伏击摘录",
                        "source_text": "轮到地精行动时，其中两个地精冲出灌木丛。打败伏击的地精并发现克拉摩窝点后，每人75 XP。",
                        "subsections": [{"title": "奖励经验值", "text": "每人75 XP"}],
                        "dm_summary": "四只地精伏击。",
                        "player_visible_intro": "两匹死马挡在路上。",
                        "secrets": ["地精躲在灌木丛"],
                        "checks": [{"ability": "wis", "skill": "survival", "dc": 10}],
                        "encounters": [{"monster_slug": "goblin", "count": 4}],
                        "rewards": [{"type": "xp", "description": "75 XP"}],
                        "clues": [{"id": "goblin_trail", "label": "地精踪迹"}],
                        "events": [],
                        "dm_guidance": {"tactics": ["两近战两远程"], "xp": ["75 XP"]},
                        "rules_overrides": [{"topic": "surprise", "rule": "新版突袭只让先攻劣势"}],
                    },
                    "progression_rule": "剧情推进出口只看顶层 available_exits；available_exits 非空且 available=true 时即可用对应 id 调用 advance。",
                    "available_exits": [{"id": "investigate_goblin_trail", "available": False}],
                    "adventure_state": {"known_clue_ids": []},
                },
                ensure_ascii=False,
            ),
            tool_call_id="call_adventure",
            name="load_adventure_node",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("轮到地精行动时", projected)
        self.assertIn("goblin_trail", projected)
        self.assertIn("新版突袭只让先攻劣势", projected)
        self.assertIn("剧情推进出口只看顶层 available_exits", projected)
        self.assertIn("available_exits", projected)
        self.assertNotIn("candidate_exits", projected)

    def test_adventure_search_block_warns_candidates_are_not_current_scene(self):
        assembler = ContextAssembler()
        state = {
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "phandalin",
                "unlocked_node_ids": ["phandalin"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "claimed_reward_ids": [],
                "pending_exit_option_ids": [],
                "breadcrumb_node_ids": ["phandalin"],
                "deferred_node_ids": [],
                "transition_log": [],
            },
            "messages": [HumanMessage(content="我想问问克拉摩窝点的事。")],
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="narrative rules")

        self.assertIn("[冒险节点检索]", assembled.runtime_state_text)
        self.assertIn("不得把候选节点当作已经发生的场景", assembled.runtime_state_text)

    def test_adventure_node_fact_block_hides_runtime_xp_instructions(self):
        provider = AdventureNodeRetrievalContextProvider()
        node = SimpleNamespace(
            id="cragmaw_hideout_entrance",
            title="克拉摩窝点入口",
            source_refs=[],
            page_start=6,
            page_end=7,
            dm_summary="入口溪水流入洞穴。抵达此处时结算75 XP里程碑经验。",
            player_visible_intro="洞口前有浅溪。",
            scene_beats=["先呈现洞口地形。", "在进入窝点探索前结算里程碑经验。"],
            rules_notes=["洞内完全黑暗。", "只有在打败伏击地精并发现窝点后才发放75 XP。"],
            clues=[],
            exits=[],
            fallbacks=[],
        )

        block = provider._build_current_node_block(
            node,
            {
                "breadcrumb_node_ids": ["goblin_ambush", "cragmaw_hideout_entrance"],
                "deferred_node_ids": [],
            },
        )

        self.assertIn("入口溪水流入洞穴", block)
        self.assertIn("洞内完全黑暗", block)
        self.assertNotIn("75 XP", block)
        self.assertNotIn("里程碑经验", block)

    def test_pending_adventure_reward_is_visible_as_claimable_work(self):
        assembler = ContextAssembler()
        state = {
            "player": {"id": "player", "name": "玩家", "xp": 75},
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
                "breadcrumb_node_ids": ["goblin_ambush", "cragmaw_hideout_entrance"],
                "deferred_node_ids": [],
                "transition_log": [],
            },
        }

        assembled = assembler.assemble(state, NARRATIVE_AGENT_MODE, base_system_prompt="narrative rules")

        self.assertIn("待发放剧情奖励:", assembled.runtime_state_text)
        self.assertIn("goblin_ambush_hideout_75_xp", assembled.runtime_state_text)
        self.assertIn("当前必须执行: 本轮第一步调用 claim_adventure_reward", assembled.runtime_state_text)

    def test_spider_web_route_memory_hides_stale_opening_nodes(self):
        provider = AdventureNodeRetrievalContextProvider()
        block = provider._build_route_memory_block(
            {
                "module_id": "lost_mine",
                "active_node_id": "spider_web_overview",
                "unlocked_node_ids": ["goblin_ambush", "cragmaw_hideout_klarg_cave", "phandalin", "spider_web_overview"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "claimed_reward_ids": [],
                "pending_exit_option_ids": [],
                "breadcrumb_node_ids": [
                    "adventure_hook_meet_me_in_phandalin",
                    "goblin_ambush",
                    "cragmaw_hideout_entrance",
                    "cragmaw_hideout_klarg_cave",
                    "phandalin",
                    "spider_web_overview",
                ],
                "deferred_node_ids": ["goblin_ambush", "cragmaw_hideout_klarg_cave", "phandalin"],
                "transition_log": [],
            }
        )

        self.assertIn("phandalin", block)
        self.assertNotIn("goblin_ambush", block)
        self.assertNotIn("cragmaw_hideout_entrance", block)
        self.assertNotIn("cragmaw_hideout_klarg_cave", block)

    def test_wave_echo_route_memory_hides_stale_spider_web_nodes(self):
        provider = AdventureNodeRetrievalContextProvider()
        block = provider._build_route_memory_block(
            {
                "module_id": "lost_mine",
                "active_node_id": "wave_echo_forge_of_spells",
                "unlocked_node_ids": ["phandalin", "old_owl_well", "cragmaw_castle_search", "wave_echo_forge_of_spells"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "claimed_reward_ids": [],
                "pending_exit_option_ids": [],
                "breadcrumb_node_ids": [
                    "phandalin",
                    "old_owl_well",
                    "cragmaw_castle_search",
                    "wave_echo_overview",
                    "wave_echo_forge_of_spells",
                ],
                "deferred_node_ids": ["old_owl_well", "cragmaw_castle_search", "phandalin", "wave_echo_starry_cavern"],
                "transition_log": [],
            }
        )

        self.assertIn("phandalin", block)
        self.assertIn("wave_echo_overview", block)
        self.assertIn("wave_echo_starry_cavern", block)
        self.assertNotIn("old_owl_well", block)
        self.assertNotIn("cragmaw_castle_search", block)

    def test_combat_archive_metadata_no_longer_collapses_model_history(self):
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

        projected_text = "\n".join(str(message.content) for message in assembled.model_input_messages)
        self.assertNotIn("[系统:战斗归档]", projected_text)
        self.assertIn("战斗开始！第 1 回合。", projected_text)
        self.assertIn("共进行了 1 回合。 倒下: Wolf", projected_text)
        self.assertEqual("我检查四周。", assembled.model_input_messages[-1].content)

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

        self.assertEqual("战斗规则", assembled.system_prompt)
        self.assertIn("[战斗状态]", assembled.runtime_state_text)
        self.assertIn("[当前回合指令]", assembled.runtime_state_text)
        self.assertIn("Goblin", assembled.runtime_state_text)

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

        self.assertIn("actions:Scimitar(scimitar, attack)", assembled.runtime_state_text)
        self.assertIn("actions=[Scimitar(scimitar, attack)", assembled.hud_text)
        self.assertNotIn("attacks:[", assembled.runtime_state_text)

    def test_combat_context_separates_allies_and_projects_resources(self):
        assembler = ContextAssembler()
        state = {
            "messages": [HumanMessage(content="继续战斗。")],
            "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 10, "max_hp": 12, "ac": 16},
            "combat": {
                "round": 1,
                "current_actor_id": "apprentice_wizard",
                "initiative_order": ["apprentice_wizard", "goblin_1", "player_hero"],
                "participants": {
                    "apprentice_wizard": {
                        "id": "apprentice_wizard",
                        "name": "伊莲",
                        "side": "ally",
                        "hp": 12,
                        "max_hp": 12,
                        "ac": 12,
                        "resources": {"spell_slot_lv1": 2},
                        "resource_caps": {"spell_slot_lv1": 3},
                        "known_spells": ["magic_missile", "shield"],
                        "known_cantrips": ["fire_bolt"],
                        "reaction_available": True,
                        "attacks": [{"name": "Dagger"}],
                        "behavior_profile": "保持距离并保护玩家。",
                    },
                    "goblin_1": {"name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7, "ac": 15},
                },
            },
        }

        assembled = assembler.assemble(state, COMBAT_AGENT_MODE, base_system_prompt="战斗规则")

        self.assertIn("友方侧: 伊莲", assembled.runtime_state_text)
        self.assertIn("对立侧: Goblin", assembled.runtime_state_text)
        self.assertIn("spell_slot_lv1=2/3", assembled.runtime_state_text)
        self.assertIn("magic:spells=magic_missile,shield; cantrips=fire_bolt", assembled.runtime_state_text)
        self.assertIn("magic_missile", assembled.hud_text)
        self.assertIn("reaction=可用: shield", assembled.hud_text)
        self.assertIn("当前是友方单位 伊莲", assembled.runtime_state_text)
        self.assertIn("以该友方单位 ID 作为行动者/施法者", assembled.runtime_state_text)

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

    def test_cast_spell_projection_keeps_multi_target_hp_results(self):
        tool_message = ToolMessage(
            content=(
                "伊莲 施放 魔法飞弹（1环）— 3 枚飞弹!\n"
                "  → Goblin A: 2枚 [1d4+1 (2), 1d4+1 (3)] = 5 力场伤害\n"
                "  Goblin A HP: 7 → 2\n"
                "  → Goblin B: 1枚 [1d4+1 (4)] = 4 力场伤害\n"
                "  Goblin B HP: 7 → 3\n"
                "（剩余1环法术位: 2）"
            ),
            tool_call_id="call_spell",
            name="cast_spell",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("Goblin A HP: 7 → 2", projected)
        self.assertIn("Goblin B HP: 7 → 3", projected)
        self.assertIn("剩余1环法术位", projected)

    def test_manage_inventory_projection_keeps_shop_catalog_prices(self):
        tool_message = ToolMessage(
            content=(
                "[商店待售清单]\n"
                "- potion_of_healing: 治疗药水 / Potion of Healing，50 gp。饮用此药水可以恢复2d4+2点生命值。\n"
                "- potion_of_greater_healing: 强效治疗药水 / Potion of Greater Healing，200 gp。饮用此药水可以恢复4d4+4点生命值。\n"
                "- potion_of_invisibility: 隐身药水 / Potion of Invisibility，200 gp。\n"
                "- potion_of_vitality: 活力药水 / Potion of Vitality，200 gp。\n\n"
                "[交易结果]\n购物完成：购买 1 个治疗药水，花费 50 gp；剩余 25 gp。"
            ),
            tool_call_id="call_buy",
            name="manage_inventory",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("[工具:manage_inventory]", projected)
        self.assertIn("[商店待售清单]", projected)
        self.assertIn("potion_of_healing", projected)
        self.assertIn("50 gp", projected)
        self.assertIn("potion_of_greater_healing", projected)
        self.assertIn("200 gp", projected)
        self.assertIn("[交易结果]", projected)

    def test_attack_projection_keeps_hp_resolution_line(self):
        tool_message = ToolMessage(
            content=(
                "Goblin 2 使用 [Scimitar] 攻击 巴伦!\n"
                "命中骰: 2d20kh1 (10, 14)+ 4 = 18 vs AC 16\n"
                "伤害骰: 1d6 (6)+ 2 = 8 → 8 点 slashing 伤害\n"
                "巴伦 HP: 12 → 4"
            ),
            tool_call_id="call_attack",
            name="use_monster_action",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("巴伦 HP: 12 → 4", projected)
        self.assertIn("已经由工具写回", projected)
        self.assertIn("不得再次扣减", projected)

    def test_any_tool_projection_keeps_hp_resolution_line(self):
        tool_message = ToolMessage(
            content=(
                "巴伦饮下治疗药水。\n"
                "治疗骰: 2d4+2 = 7\n"
                "巴伦 HP: 4 → 11"
            ),
            tool_call_id="call_state",
            name="modify_character_state",
        )

        projected = summarize_tool_message(tool_message)

        self.assertIn("巴伦 HP: 4 → 11", projected)
        self.assertIn("不得再次扣减、治疗或改写 HP", projected)


if __name__ == "__main__":
    unittest.main()
