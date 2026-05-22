"""上下文装配器。"""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.adventures.clue_projection import format_known_clue_window, project_known_clue_window
from app.adventures.navigation import normalize_adventure_state, recent_breadcrumb_ids, returnable_node_ids
from app.adventures.rewards import normalize_pending_reward_grants
from app.adventures.store import get_adventure_store
from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.graph.state import GraphState
from app.services.class_action_catalog import available_class_actions
from app.services.tools._helpers import compute_ac


CONTEXT_COMPACTION_MESSAGE_PREFIX = "[系统:上下文预算归档]"
RUNTIME_STATE_MESSAGE_PREFIX = "[系统:运行状态帧]"
ADVENTURE_NODE_FRAME_MESSAGE_PREFIX = "[系统:冒险节点帧]"
MODEL_CONTEXT_TOKEN_LIMIT = 1_000_000
# 中文注释：前缀保持稳定时，宁可更早压缩，也不要让长历史把有效前缀拖得太贵。
CONTEXT_SOFT_COMPACT_TOKEN_BUDGET = 450_000
CONTEXT_HARD_TRIM_TOKEN_BUDGET = 600_000
CONTEXT_COMPACTION_RETENTION_RATIO = 0.3
CONTEXT_COMPACTION_MAX_CHARS = 120_000
CONTEXT_COMPACTION_MIN_CHARS = 1200
APPROX_CHARS_PER_TOKEN = 2.0


class ExternalContextProvider(Protocol):
    """为未来的外部 RAG 能力预留注入口，当前默认不返回任何片段。"""

    def get_context_blocks(self, *, state: GraphState, mode: str) -> list[str]: ...


class NoopExternalContextProvider:
    def get_context_blocks(self, *, state: GraphState, mode: str) -> list[str]:
        return []


@dataclass(frozen=True, slots=True)
class OptionalContextBlock:
    """机会型上下文只服务本轮调用，不能写入历史消息或替代必需状态。"""

    title: str
    content: str
    source: str = "optional"


def _node_source_pages(node: Any) -> str:
    """以 source_refs 为准展示页码；旧节点保留 page_start/page_end。"""
    refs = getattr(node, "source_refs", []) or []
    page_starts: list[int] = []
    page_ends: list[int] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        try:
            page_starts.append(int(ref.get("page_start")))
            page_ends.append(int(ref.get("page_end")))
        except (TypeError, ValueError):
            continue
    if page_starts and page_ends:
        start = min(page_starts)
        end = max(page_ends)
        return str(start) if start == end else f"{start}-{end}"
    page_start = getattr(node, "page_start", "")
    page_end = getattr(node, "page_end", "")
    if page_start and page_end:
        return str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
    return "未知"


def _compact_lines(values: list[Any], limit: int, *, char_limit: int = 120) -> list[str]:
    """节点卡片只投影最关键几条，完整内容仍保留在 load_node 工具结果里。"""
    return [compact_text(str(item), char_limit) for item in values[:limit] if str(item).strip()]


def _compact_non_xp_lines(values: list[Any], limit: int, *, char_limit: int = 120) -> list[str]:
    """XP 结算由冒险 runtime 接管，节点卡片只保留可演绎的场景与规则。"""
    filtered = [item for item in values if not _looks_like_xp_reward_note(item)]
    return _compact_lines(filtered, limit, char_limit=char_limit)


def _looks_like_xp_reward_note(value: Any) -> bool:
    """避免把结构化奖励元数据重新包装成给主模型的发放指令。"""
    text = str(value).lower()
    return "xp" in text or "经验" in text or ("奖励" in text and ("发放" in text or "结算" in text))


def _sanitize_adventure_summary(text: str) -> str:
    """把里程碑金额从节点摘要里拿掉，保留可演绎的剧情事实。"""
    if not text:
        return ""
    sentences = [segment.strip() for segment in text.replace("\n", "。").split("。") if segment.strip()]
    cleaned: list[str] = []
    reward_markers = ("xp", "经验", "里程碑", "发放", "结算")
    for sentence in sentences:
        if any(marker in sentence.lower() for marker in reward_markers):
            continue
        cleaned.append(sentence)
    if cleaned:
        return "。".join(cleaned) + "。"
    if any(marker in text.lower() for marker in reward_markers):
        return "相关里程碑奖励由专用奖励工具发放。"
    return text


def _format_pending_reward_grants(values: list[Any] | None, limit: int = 4) -> str:
    """把待领取奖励压成可操作短句，提醒主模型先写回再叙事。"""
    rewards = normalize_pending_reward_grants(values)
    if not rewards:
        return ""
    lines: list[str] = []
    for reward in rewards[:limit]:
        reward_type = reward.get("type", "")
        amount = reward.get("amount", 0)
        description = compact_text(str(reward.get("description", "")), 80)
        label = f"{reward['id']}: {amount} {str(reward_type).upper()}".strip()
        if description:
            label += f"（{description}）"
        lines.append(label)
    if len(rewards) > limit:
        lines.append(f"另有 {len(rewards) - limit} 项")
    return "；".join(lines)


def _compact_ids(values: list[Any], limit: int) -> list[str]:
    """长 ID 列表只保留前几项，后面的数量交给计数提示。"""
    ids = [str(item).strip() for item in values if str(item).strip()]
    if limit <= 0:
        return []
    return ids[:limit]


def _compact_fallbacks(values: list[Any], limit: int) -> list[str]:
    """回退分支保留条件和裁定意图，帮助模型处理绕路。"""
    fallbacks: list[str] = []
    for item in values[:limit]:
        if not isinstance(item, dict):
            continue
        fallback_id = str(item.get("id", "")).strip()
        condition = compact_text(str(item.get("condition", "")), 100)
        guidance = compact_text(str(item.get("dm_guidance", "")), 120)
        if _looks_like_xp_reward_note(guidance):
            guidance = "奖励条件由后台待发放队列判定；继续按实际情况主持。"
        label = fallback_id or "fallback"
        if condition and guidance:
            fallbacks.append(f"{label}: {condition} -> {guidance}")
        elif condition:
            fallbacks.append(f"{label}: {condition}")
    return fallbacks


class AdventureNodeRetrievalContextProvider:
    """给冒险节点加一层轻量 RAG，专门喂当前节点和相邻命中。"""

    def get_context_blocks(self, *, state: GraphState, mode: str) -> list[str]:
        if mode != NARRATIVE_AGENT_MODE:
            return []

        adventure_dict = normalize_adventure_state(state_value_to_dict(state.get("adventure")) or {})
        if not adventure_dict.get("active_node_id"):
            return []

        try:
            node = get_adventure_store().get_node(adventure_dict["active_node_id"])
        except Exception:
            return []

        blocks = [self._build_current_node_block(node, adventure_dict)]
        route_block = self._build_route_memory_block(adventure_dict)
        if route_block:
            blocks.append(route_block)

        query = _latest_external_human_text(state.get("messages", []))
        if query:
            related_nodes = [
                item
                for item in get_adventure_store().search_nodes(query, limit=3)
                if item[0].id != node.id
            ]
            if related_nodes:
                blocks.append(self._build_search_block(query, related_nodes))

        return blocks

    def _build_current_node_block(self, node: Any, adventure: dict[str, Any]) -> str:
        exits = [f"{item.id}: {item.label} -> {getattr(item, 'next_node_id', '')}" for item in getattr(node, "exits", [])]
        clue_ids = [str(item.get("id", "")) for item in getattr(node, "clues", []) if isinstance(item, dict) and item.get("id")]
        source_pages = _node_source_pages(node)
        scene_beats = _compact_non_xp_lines(getattr(node, "scene_beats", []), 3)
        rules_notes = _compact_non_xp_lines(getattr(node, "rules_notes", []), 2)
        routing_notes = _compact_non_xp_lines(getattr(node, "routing_notes", []), 2, char_limit=100)
        fallbacks = _compact_fallbacks(getattr(node, "fallbacks", []), 3)
        lines = [
            "[冒险节点事实]",
            f"当前节点: {node.title} [ID:{node.id}]",
            f"页码: {source_pages}",
            f"节点摘要: {_sanitize_adventure_summary(compact_text(node.dm_summary, 240))}",
        ]
        if getattr(node, "player_visible_intro", ""):
            lines.append(f"开场可见: {compact_text(node.player_visible_intro, 180)}")
        if scene_beats:
            lines.append("关键推进: " + " | ".join(scene_beats))
        if rules_notes:
            lines.append("规则提醒: " + " | ".join(rules_notes))
        if routing_notes:
            lines.append("路线原则: " + " | ".join(routing_notes))
        if clue_ids:
            preview = _compact_ids(clue_ids, 8)
            lines.append(
                "节点线索: "
                + ", ".join(preview)
                + (f"（另有 {len(clue_ids) - len(preview)} 条）" if len(clue_ids) > len(preview) else "")
            )
        if exits:
            lines.append(f"节点出口: {'；'.join(exits)}")
        if fallbacks:
            lines.append("回退分支: " + "；".join(fallbacks))
        if len(adventure.get("breadcrumb_node_ids", [])) > 1 or adventure.get("deferred_node_ids"):
            deferred_lines = self._summarize_route_nodes(adventure.get("deferred_node_ids", [])[:6])
            if deferred_lines:
                lines.append(f"待回访节点: {'；'.join(deferred_lines)}")
        return "\n".join(lines)

    def _build_route_memory_block(self, adventure: dict[str, Any]) -> str:
        returnable_ids = returnable_node_ids(adventure, adventure.get("active_node_id", ""))
        visible_route_ids = set(returnable_ids)
        visible_route_ids.add(str(adventure.get("active_node_id", "")))
        breadcrumb_ids = [node_id for node_id in recent_breadcrumb_ids(adventure, limit=6) if node_id in visible_route_ids]
        if len(breadcrumb_ids) <= 1 and not adventure.get("deferred_node_ids"):
            return ""

        lines = ["[冒险路径记忆]"]
        if breadcrumb_ids:
            lines.append("路径回溯: " + " -> ".join(self._summarize_route_nodes(breadcrumb_ids)))
        if adventure.get("deferred_node_ids"):
            deferred = self._summarize_route_nodes([node_id for node_id in adventure.get("deferred_node_ids", []) if node_id in visible_route_ids][:6])
            if deferred:
                lines.append("待回访节点: " + "；".join(deferred))
        returnable = self._summarize_route_nodes(returnable_ids[:6])
        if returnable:
            lines.append("可回访节点: " + "；".join(returnable))
        return "\n".join(lines)

    def _summarize_route_nodes(self, node_ids: list[str]) -> list[str]:
        labels: list[str] = []
        for node_id in node_ids:
            try:
                node = get_adventure_store().get_node(node_id)
            except Exception:
                labels.append(node_id)
                continue
            labels.append(f"{node.title}[ID:{node.id}]")
        return labels

    def _build_search_block(self, query: str, results: list[tuple[Any, float]]) -> str:
        lines = [
            "[冒险节点检索]",
            f"最近意图: {compact_text(query, 120)}",
            "约束: 以下仅是候选资料；不得把候选节点当作已经发生的场景。只有当前节点出口、路径回访或后台运行指令能改变当前位置。",
        ]
        for node, score in results:
            exits = [item.label for item in getattr(node, "exits", [])]
            lines.append(
                f"- {node.id} | {node.title} | 页 {node.page_start}-{node.page_end} | "
                f"score={score:.1f} | 摘要={compact_text(node.dm_summary, 140)}"
            )
            if exits:
                lines.append(f"  出口: {'；'.join(exits[:3])}")
        return "\n".join(lines)


@dataclass(slots=True)
class RequiredContext:
    """主模型调用的必需上下文；剧情、战斗与工具状态都必须稳定进入这里。"""

    system_prompt: str
    hud_text: str
    runtime_state_text: str
    model_input_messages: list[BaseMessage]


@dataclass(slots=True)
class AssembledContext(RequiredContext):
    """保留旧类型名，避免调用方和测试在拆分期间感知实现细节。"""


class ContextAssembler:
    """统一拼装系统提示、HUD 和模型可见消息窗口。"""

    def __init__(self, external_context_provider: ExternalContextProvider | None = None) -> None:
        self._external_context_provider = external_context_provider or AdventureNodeRetrievalContextProvider()

    def assemble(self, state: GraphState, mode: str, *, base_system_prompt: str) -> AssembledContext:
        """把图状态投影为一次模型调用所需的完整上下文。"""
        required = self.assemble_required(state, mode, base_system_prompt=base_system_prompt)
        return AssembledContext(
            system_prompt=required.system_prompt,
            hud_text=required.hud_text,
            runtime_state_text=required.runtime_state_text,
            model_input_messages=required.model_input_messages,
        )

    def assemble_required(self, state: GraphState, mode: str, *, base_system_prompt: str) -> RequiredContext:
        """只组装不可丢弃上下文，供后续机会型 RAG 在外层追加短期证据。"""
        hud_text = self.build_hud_text(state)
        runtime_state_text = self.build_runtime_state_text(state, mode)
        return RequiredContext(
            system_prompt=self.build_system_prompt(state, mode, base_system_prompt),
            hud_text=hud_text,
            runtime_state_text=runtime_state_text,
            model_input_messages=self.build_model_input_messages(state, mode, runtime_state_text),
        )

    def append_optional_runtime_context(
        self,
        required_context: RequiredContext,
        optional_blocks: list[OptionalContextBlock],
    ) -> AssembledContext:
        """把赶上的机会型证据附加到本轮状态帧，不重建历史消息窗口。"""
        rendered_blocks = [self._format_optional_context_block(block) for block in optional_blocks if block.content.strip()]
        if not rendered_blocks:
            return AssembledContext(
                system_prompt=required_context.system_prompt,
                hud_text=required_context.hud_text,
                runtime_state_text=required_context.runtime_state_text,
                model_input_messages=required_context.model_input_messages,
            )

        runtime_state_text = required_context.runtime_state_text + "\n\n[机会型上下文]\n" + "\n\n".join(rendered_blocks)
        return AssembledContext(
            system_prompt=required_context.system_prompt,
            hud_text=required_context.hud_text,
            runtime_state_text=runtime_state_text,
            model_input_messages=required_context.model_input_messages,
        )

    def _format_optional_context_block(self, block: OptionalContextBlock) -> str:
        """保留来源标签，方便 trace 对照自动规则证据是否被注入。"""
        return f"[{block.title} | source={block.source}]\n{block.content.strip()}"

    def build_system_prompt(self, state: GraphState, mode: str, base_system_prompt: str) -> str:
        # 中文注释：系统提示词只保留稳定规则，避免每轮状态变化破坏 DeepSeek 前缀缓存。
        return base_system_prompt

    def build_runtime_state_text(self, state: GraphState, mode: str, hud_text: str = "") -> str:
        """生成模型每轮必须看到的短状态帧，完整 HUD 只保留给前端和 trace。"""
        sections: list[str] = []

        sections.append("[事实优先级]\n本帧 > 最新工具返回 > 旧对话。旧 HUD、旧战报和旧资源数字只作历史叙事参考。")
        sections.append(self._build_runtime_mode_frame(state, mode))

        if mode == COMBAT_AGENT_MODE:
            combat_brief = self._build_combat_frame(state)
            if combat_brief:
                sections.append(combat_brief)

            turn_directive = self._build_combat_turn_directive(state)
            if turn_directive:
                sections.append(f"[当前回合指令]\n{turn_directive}")
        else:
            narrative_frame = self._build_narrative_state_frame(state)
            if narrative_frame:
                sections.append(narrative_frame)

        if mode == NARRATIVE_AGENT_MODE and needs_opening_fighter_companion(state):
            sections.append(
                "[开局友方准则]\n"
                "当前玩家已加载但尚无友方单位。应在合理的开局叙事中主动创建一名名为格林的战士友方，"
                "使用友方创建能力生成 fighter_companion；格林可靠沉稳，是玩家的开局同行队友，不是修达·霍温特。"
                "创建后再继续推进冒险。"
            )

        runtime_directive = self._build_adventure_runtime_directive(state, mode)
        if runtime_directive:
            sections.append(runtime_directive)

        guardrail_warning = self._build_adventure_guardrail_warning(state, mode)
        if guardrail_warning:
            sections.append(guardrail_warning)

        external_blocks = self._external_context_provider.get_context_blocks(state=state, mode=mode)
        if external_blocks:
            sections.append("[扩展上下文]\n" + "\n\n".join(block for block in external_blocks if block))

        adventure_anchor = self._build_adventure_node_anchor(state, mode)
        if adventure_anchor:
            sections.append(adventure_anchor)

        return "\n\n".join(block for block in sections if block)

    def build_hud_text(self, state: GraphState) -> str:
        sections: list[str] = []

        sections.append(
            "[当前冒险状态]\n"
            + format_adventure_summary(
                state_value_to_dict(state.get("adventure")),
                visible_clue_ids=state_value_to_dict(state.get("adventure_visible_clue_ids")),
            )
        )

        player_dict = state_value_to_dict(state.get("player"))
        if player_dict:
            sections.append(
                "[当前玩家状态]\n"
                + format_player_priority_summary(player_dict)
                + "\n"
                + json.dumps(player_dict, ensure_ascii=False, indent=2)
            )
        else:
            sections.append("[当前玩家状态]\n玩家尚未加载或创建角色卡。")

        combat_dict = state_value_to_dict(state.get("combat"))
        if combat_dict:
            current_id = combat_dict.get("current_actor_id", "")
            participants = dict(combat_dict.get("participants", {}))
            if player_dict and player_dict.get("id"):
                participants[player_dict["id"]] = player_dict

            combat_lines = [
                f"第 {combat_dict.get('round', '?')} 回合 | 当前行动者: {current_id}",
                f"先攻顺序: {combat_dict.get('initiative_order', [])}",
            ]
            for uid, combatant in participants.items():
                actions_desc = format_actions(combatant)
                marker = " ← 当前行动" if uid == current_id else ""
                display_ac = compute_ac(combatant) if isinstance(combatant, dict) else combatant.get('ac')
                combat_lines.append(
                    f"  {combatant.get('name', uid)} [ID:{uid}] side={combatant.get('side')} "
                    f"HP:{combatant.get('hp')}/{combatant.get('max_hp')} AC:{display_ac} "
                    f"conditions=[{format_conditions(combatant)}] resources=[{format_resources(combatant)}] "
                    f"death_saves={format_death_save_status(combatant)} surprised={bool(combatant.get('surprised'))} "
                    f"reaction={format_reaction_status(combatant)} magic=[{format_magic(combatant)}] "
                    f"actions=[{actions_desc}] behavior=[{format_behavior_profile(combatant)}]{marker}"
                )
            sections.append("[当前战斗状态]\n" + "\n".join(combat_lines))

        scene_data = dump_mapping_state(state.get("scene_units"))
        if scene_data:
            scene_lines = [
                f"  ID:{uid} name={unit.get('name', uid)} side={unit.get('side')} "
                f"start_combat.combatant_ids 可直接使用 \"{uid}\" "
                f"(HP:{unit.get('hp')}/{unit.get('max_hp')}, "
                f"resources=[{format_resources(unit)}], "
                f"magic=[{format_magic(unit)}], behavior=[{format_behavior_profile(unit)}])"
                for uid, unit in scene_data.items()
            ]
            sections.append(
                "[场景单位池（start_combat 的非玩家参战 ID 来源；敌人和友方都必须显式列入 combatant_ids）]\n"
                + "\n".join(scene_lines)
            )

        space_data = state_value_to_dict(state.get("space"))
        sections.append("[当前平面空间]\n" + format_space_summary(space_data))

        dead_data = dump_mapping_state(state.get("dead_units"))
        if dead_data:
            dead_lines = [f"  {uid}: {unit.get('name', uid)}" for uid, unit in dead_data.items()]
            sections.append("[死亡单位档案]\n" + "\n".join(dead_lines))

        return "\n\n=== 状态快照 ===\n" + "\n\n".join(sections) + "\n===========================\n"

    def build_model_input_messages(self, state: GraphState, mode: str, runtime_state_text: str) -> list[BaseMessage]:
        # DeepSeek KC 成本可接受时，战后也保留原始战斗记录，避免摘要过弱导致模型误以为战斗未发生。
        source_messages = list(state.get("messages", []))
        trimmed_messages = trim_model_messages(source_messages, mode, state=state)
        projected_messages: list[BaseMessage] = []

        for message in trimmed_messages:
            if isinstance(message, ToolMessage):
                projected_messages.append(clone_message_with_content(message, summarize_tool_message(message)))
                continue

            if isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.startswith("[系统:"):
                if message.content.startswith(RUNTIME_STATE_MESSAGE_PREFIX):
                    continue
                if message.content.startswith(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX):
                    continue
                if message.content.startswith(CONTEXT_COMPACTION_MESSAGE_PREFIX):
                    projected_messages.append(message)
                    continue
                projected_messages.append(clone_message_with_content(message, summarize_system_message(message.content)))
                continue

            projected_messages.append(message)

        repaired_messages = repair_tool_call_sequence(projected_messages)
        return repaired_messages

    def _build_combat_brief(self, state: GraphState) -> str:
        combat_dict = state_value_to_dict(state.get("combat"))
        if not combat_dict:
            return ""

        player_dict = state_value_to_dict(state.get("player"))
        participants = dict(combat_dict.get("participants", {}))
        if player_dict and player_dict.get("id"):
            participants[player_dict["id"]] = player_dict

        current_id = combat_dict.get("current_actor_id", "")
        current_actor = participants.get(current_id, {})
        lines = [
            f"第 {combat_dict.get('round', '?')} 回合，当前行动者 {current_actor.get('name', current_id)} [ID:{current_id}]。",
            f"先攻顺序: {combat_dict.get('initiative_order', [])}",
        ]

        if scene_summary := state.get("scene_summary"):
            lines.append(f"当前局势/战斗 stakes: {scene_summary}")

        player_side: list[str] = []
        ally_side: list[str] = []
        enemy_side: list[str] = []
        other_side: list[str] = []
        for uid, combatant in participants.items():
            display_ac = compute_ac(combatant) if isinstance(combatant, dict) else combatant.get('ac')
            status = (
                f"{combatant.get('name', uid)}[HP:{combatant.get('hp')}/{combatant.get('max_hp')}, "
                f"AC:{display_ac}, conditions:{format_conditions(combatant)}, "
                f"resources:{format_resources(combatant)}, death_saves:{format_death_save_status(combatant)}, "
                f"surprised:{bool(combatant.get('surprised'))}, "
                f"reaction:{format_reaction_status(combatant)}, magic:{format_magic(combatant)}, "
                f"actions:{format_actions(combatant)}]"
            )
            if combatant.get("side") == "player":
                player_side.append(status)
            elif combatant.get("side") == "ally":
                ally_side.append(status)
            elif combatant.get("side") == "enemy":
                enemy_side.append(status)
            else:
                other_side.append(status)

        if player_side:
            lines.append("玩家侧: " + "；".join(player_side))
        if ally_side:
            lines.append("友方侧: " + "；".join(ally_side))
        if enemy_side:
            lines.append("对立侧: " + "；".join(enemy_side))
        if other_side:
            lines.append("中立/其他: " + "；".join(other_side))

        return "\n".join(lines)

    def _build_runtime_mode_frame(self, state: GraphState, mode: str) -> str:
        """把当前模式和基础角色状态压成短帧，替代过去的大块 HUD。"""
        lines = [f"模式: {mode}"]

        player_dict = state_value_to_dict(state.get("player"))
        if player_dict:
            lines.append(
                "玩家: "
                f"{player_dict.get('name', player_dict.get('id', 'player'))} [ID:{player_dict.get('id', 'player')}] "
                f"{format_player_priority_summary(player_dict)} "
                f"conditions:{format_conditions(player_dict)} "
                f"items:{format_consumables(player_dict)} "
                f"magic:[{format_magic(player_dict)}]"
            )
        else:
            lines.append("玩家: 尚未加载角色卡。")

        adventure_dict = normalize_adventure_state(state_value_to_dict(state.get("adventure")) or {})
        lines.append(f"冒险节点: {adventure_dict['module_id']} / {adventure_dict['active_node_id']}")
        if adventure_dict.get("known_clue_ids"):
            clue_window = _visible_adventure_clue_window(
                adventure_dict,
                visible_clue_ids=state_value_to_dict(state.get("adventure_visible_clue_ids")),
                query=_latest_external_human_text(state.get("messages", [])),
            )
            lines.append(
                "已知线索: "
                + format_known_clue_window(clue_window, total_count=len(adventure_dict["known_clue_ids"]))
            )
        if adventure_dict.get("completed_event_ids"):
            lines.append(f"已完成事件: {adventure_dict['completed_event_ids']}")
        if len(adventure_dict.get("breadcrumb_node_ids", [])) > 1:
            lines.append(f"路径回溯: {recent_breadcrumb_ids(adventure_dict, limit=6)}")
        if adventure_dict.get("deferred_node_ids"):
            lines.append(f"待回访节点: {adventure_dict['deferred_node_ids'][:6]}")
        if adventure_dict.get("pending_exit_option_ids"):
            lines.append(f"待确认出口: {adventure_dict['pending_exit_option_ids']}")
        pending_rewards = _format_pending_reward_grants(adventure_dict.get("pending_reward_grants"))
        if pending_rewards:
            lines.append(f"待发放剧情奖励: {pending_rewards}")
            lines.append(
                "当前必须执行: 本轮第一步调用 claim_adventure_reward 领取最前面的待发放奖励；"
                "工具成功后再向玩家确认并继续叙事。不要口头宣告未写入的奖励。"
            )

        return "[运行状态帧]\n" + "\n".join(lines)

    def _build_adventure_guardrail_warning(self, state: GraphState, mode: str) -> str:
        """只给模型短流程约束；详细越界审计留在 trace/state，避免被复述给玩家。"""
        if mode != NARRATIVE_AGENT_MODE:
            return ""
        warning = state_value_to_dict(state.get("adventure_guardrail_warning"))
        if not warning:
            return ""
        lines = [
            "[内部冒险校准]",
            "上一轮存在未确认的冒险内容；这是内部流程约束，不得向玩家复述或解释。",
            f"当前可信节点: {warning.get('node_id', '')}。",
            "本轮若继续模组剧情，先按当前冒险节点事实重整叙事，再自然回应。",
        ]
        return "\n".join(lines)

    def _build_adventure_runtime_directive(self, state: GraphState, mode: str) -> str:
        """后台推进节点后，用最短指令要求主模型先读取新节点。"""
        if mode != NARRATIVE_AGENT_MODE:
            return ""
        directive = state_value_to_dict(state.get("adventure_runtime_directive"))
        if not directive:
            return ""
        if directive.get("kind") not in {"node_advanced", "node_switched", "node_revisited"}:
            return ""
        kind = directive.get("kind")
        verb = "推进到" if kind == "node_advanced" else "回访到" if kind == "node_revisited" else "切换到"
        return (
            "[内部冒险流程]\n"
            f"后台已将冒险书签{verb} {directive.get('node_id', '')}。"
            "这是内部流程约束，不得向玩家复述或解释。"
            "本轮第一步先按当前节点事实重整叙事，只依据新节点事实继续主持。"
        )

    def _build_narrative_state_frame(self, state: GraphState) -> str:
        """探索态只给模型焦点摘要；完整地图和单位细节留给工具按需读取。"""
        lines: list[str] = []
        scene_data = dump_mapping_state(state.get("scene_units"))
        if scene_data:
            allies = [format_narrative_ally_snapshot(uid, unit) for uid, unit in scene_data.items() if unit.get("side") == "ally"]
            enemies = [f'{unit.get("name", uid)}[ID:{uid}]' for uid, unit in scene_data.items() if unit.get("side") == "enemy"]
            if allies:
                lines.append("可见友方: " + "；".join(allies[:4]))
            if enemies:
                lines.append("可见敌对/怪物: " + "；".join(enemies[:6]))
            magic_units = format_scene_magic_snapshots(scene_data)
            if magic_units:
                lines.append("可用法术单位: " + magic_units)

        space_data = state_value_to_dict(state.get("space"))
        active_map = active_space_map_summary(space_data)
        if active_map:
            lines.append(active_map)
        else:
            lines.append("空间: 未建图；若叙事涉及位置、距离、范围、入场或移动，先建立或切换地图。")

        if state_value_to_dict(state.get("pending_combat_start")):
            lines.append("开战计划已提交；等待工作流统一校验并投先攻，不要重复提交。")
        else:
            lines.append(
                "开战边界: 如果敌人或玩家即将实际攻击、伏击暴露或双方需要先攻/位置/回合，先生成所需单位并提交开战计划；"
                "由你决定入场怪物/友方数量、地图方案、初始落点和突袭对象，建图、摆位与先攻由工作流结算。"
            )

        lines.append("需要完整角色、单位、坐标或地图时以对应工具结果为准；节点事实已由当前节点上下文注入，不要沿用旧 HUD。")
        return "[探索状态]\n" + "\n".join(lines)

    def _build_combat_frame(self, state: GraphState) -> str:
        """战斗态只暴露回合决策所需字段，避免完整参战 JSON 每轮扰动缓存。"""
        combat_dict = state_value_to_dict(state.get("combat"))
        if not combat_dict:
            return ""

        player_dict = state_value_to_dict(state.get("player"))
        participants = dict(combat_dict.get("participants", {}))
        if player_dict and player_dict.get("id"):
            participants[player_dict["id"]] = player_dict

        current_id = combat_dict.get("current_actor_id", "")
        current_actor = participants.get(current_id, {})
        lines = [
            f"第 {combat_dict.get('round', '?')} 回合；当前行动者: {current_actor.get('name', current_id)} [ID:{current_id}] side={current_actor.get('side', '?')}",
            f"先攻顺序: {combat_dict.get('initiative_order', [])}",
        ]

        if scene_summary := state.get("scene_summary"):
            lines.append(f"当前局势: {scene_summary}")

        for label, side in (("玩家侧", "player"), ("友方侧", "ally"), ("对立侧", "enemy")):
            side_line = format_combat_side_snapshot(participants, side)
            if side_line:
                lines.append(f"{label}: {side_line}")

        lines.append("若需要完整单位详情调用 inspect_unit；坐标与移动由玩家可控回合或执行器处理，不要从旧战报推断。")
        return "[战斗状态]\n" + "\n".join(lines)

    def _build_combat_turn_directive(self, state: GraphState) -> str:
        """用共享状态显式标注当前轮到谁决策，避免模型在战斗流里漂移。"""
        combat_dict = state_value_to_dict(state.get("combat"))
        if not combat_dict:
            return ""

        player_dict = state_value_to_dict(state.get("player"))
        current_id = combat_dict.get("current_actor_id", "")
        participants = dict(combat_dict.get("participants", {}))
        if player_dict and player_dict.get("id"):
            participants[player_dict["id"]] = player_dict

        current_actor = participants.get(current_id, {})
        current_name = current_actor.get("name", current_id)
        if current_actor.get("side") == "player":
            if current_actor.get("hp", 0) <= 0:
                if current_actor.get("is_dead"):
                    return (
                        f"当前是玩家单位 {current_name} [ID:{current_id}] 的回合，但角色已经死亡。"
                        "不要执行攻击或施法；若剧情允许复活，使用 modify_character_state(action=\"revive\")，否则调用 next_turn。"
                    )
                if current_actor.get("is_stable"):
                    return (
                        f"当前是玩家单位 {current_name} [ID:{current_id}] 的回合，角色 0 HP 且伤势稳定。"
                        "不要执行攻击或施法；如无外部救援，调用 next_turn。"
                    )
                return (
                    f"当前是玩家单位 {current_name} [ID:{current_id}] 的回合，角色 0 HP，必须进行死亡豁免。"
                    "只有在玩家本轮明确表示继续或要求掷死亡豁免后，才调用 request_dice_roll(reason=\"死亡豁免\", formula=\"1d20\")，"
                    "随后用 modify_character_state(action=\"record_death_save\", payload={\"roll_total\": 掷骰raw_roll}) 写回结果；"
                    "不要执行攻击、施法或主动移动。"
                )
            return (
                f"当前是玩家单位 {current_name} [ID:{current_id}] 的回合。"
                "把玩家最新意图、输入原文和你的战术裁定一起委托战斗执行器处理具体落子；"
                "若本回合已无合理动作或前端结束回合流程帧已经推进，则尊重现有流程。"
            )

        if current_actor.get("side") == "ally":
            if current_actor.get("hp", 0) <= 0:
                if current_actor.get("is_dead"):
                    return (
                        f"当前是友方单位 {current_name} [ID:{current_id}] 的回合，但该单位已经死亡。"
                        "不要执行攻击或施法；若剧情允许复活，使用合适的治疗/复活能力，否则结束当前行动者回合。"
                    )
                if current_actor.get("is_stable"):
                    return (
                        f"当前是友方单位 {current_name} [ID:{current_id}] 的回合，该单位 0 HP 且伤势稳定。"
                        "不要执行攻击或施法；如无外部救援，结束当前行动者回合。"
                    )
                return (
                    f"当前是友方单位 {current_name} [ID:{current_id}] 的回合，该单位 0 HP，必须进行死亡豁免。"
                    "先调用 request_dice_roll(reason=\"死亡豁免\", formula=\"1d20\")，"
                    "再用 modify_character_state(action=\"record_death_save\", target_id=该友方ID, payload={\"roll_total\": 掷骰raw_roll}) 写回结果；"
                    "不要执行攻击、施法或主动移动。"
                )
            control_mode = str(current_actor.get("control_mode") or current_actor.get("controlled_by") or "").lower()
            if current_actor.get("autopilot") or current_actor.get("llm_controlled") or control_mode in {"ai", "llm", "agent"}:
                return (
                    f"当前是 AI 托管友方单位 {current_name} [ID:{current_id}] 的回合。"
                    f"行为倾向: {format_behavior_profile(current_actor)}。"
                    f"资源: {format_resources(current_actor)}；法术: {format_magic(current_actor)}；反应: {format_reaction_status(current_actor)}。"
                    "先决定本回合战术意图，再委托战斗执行器代为完成移动、攻击、施法、职业动作或物品使用；"
                    "执行器回传建议结束回合时，再推进到下一个行动者。"
                )
            return (
                f"当前是友方单位 {current_name} [ID:{current_id}] 的回合。"
                "该友方默认由玩家指导或接管；根据玩家最新意图生成执行器委托。"
                "若玩家要求其行动，把玩家原话和你的裁定一起委托战斗执行器，必须以该友方单位 ID 作为行动者/施法者，而不是玩家；"
                "若前端已经结束回合则尊重流程帧。"
            )

        return (
            f"当前是怪物/NPC {current_name} [ID:{current_id}] 的回合。"
            "你必须立刻决定战术意图并委托战斗执行器，不要等待用户继续发话；"
            "不要直接手算移动、命中、伤害或用文字宣告换人。"
            "执行器回传建议结束回合时，再推进到下一个行动者。"
        )

    def _build_adventure_node_anchor(self, state: GraphState, mode: str) -> str:
        """探索长对话中定期校准冒险节点，避免模型被聊天惯性带离模组。"""
        if mode != NARRATIVE_AGENT_MODE:
            return ""

        human_turn_count = count_external_human_turns(state.get("messages", []))
        if human_turn_count == 0 or human_turn_count % 4 != 0:
            return ""

        adventure_dict = normalize_adventure_state(state_value_to_dict(state.get("adventure")) or {})
        return (
            "[冒险节点校准]\n"
            f"当前仍处于模组 {adventure_dict['module_id']} 的节点 {adventure_dict['active_node_id']}。"
            "本轮回应前先校对玩家行动与当前节点的关系：应优先沿当前节点给出可执行后果、线索或下一步压力。"
            "若玩家明确想回到之前离开的节点，优先从路径回溯与待回访节点中找，不要把它们当作新剧情。"
            "不要顺着即兴逻辑游离到未建立的地点、NPC 或主线，也不要提前揭露未获得的信息。"
        )


def needs_opening_fighter_companion(state: GraphState) -> bool:
    """开局默认带一名战士友方；只在玩家已加载且尚无友方时提示创建。"""
    if state_value_to_dict(state.get("player")) is None:
        return False
    for unit in dump_mapping_state(state.get("scene_units")).values():
        if unit.get("side") == "ally":
            return False
    combat_dict = state_value_to_dict(state.get("combat"))
    if combat_dict:
        for unit in combat_dict.get("participants", {}).values():
            if unit.get("side") == "ally":
                return False
    return True


def trim_model_messages(messages: list[BaseMessage], mode: str, state: GraphState | None = None) -> list[BaseMessage]:
    """按 1M 上下文模型的预算裁剪；常规路径保持完整历史以换取稳定 KC 前缀。"""
    if mode == COMBAT_AGENT_MODE:
        return trim_combat_model_messages(messages, state)

    if estimate_messages_tokens(messages) <= CONTEXT_HARD_TRIM_TOKEN_BUDGET:
        return list(messages)

    return trim_messages_to_token_budget(messages, CONTEXT_SOFT_COMPACT_TOKEN_BUDGET)


def trim_combat_model_messages(messages: list[BaseMessage], state: GraphState | None = None) -> list[BaseMessage]:
    """战斗态优先保留完整前史；只有接近上下文上限时才保护活跃战斗段做预算裁剪。"""
    if not messages:
        return []

    if estimate_messages_tokens(messages) <= CONTEXT_HARD_TRIM_TOKEN_BUDGET:
        return list(messages)

    combat_start = combat_window_start_index(messages, state)
    if combat_start is None:
        return trim_messages_to_token_budget(messages, CONTEXT_SOFT_COMPACT_TOKEN_BUDGET)

    return trim_messages_to_token_budget(
        messages,
        CONTEXT_SOFT_COMPACT_TOKEN_BUDGET,
        preserve_start_index=combat_start,
    )


def combat_window_start_index(messages: list[BaseMessage], state: GraphState | None) -> int | None:
    """优先使用 start_combat 记录的归档起点；缺失时回扫最近的开战工具调用。"""
    active_start = state.get("active_combat_message_start") if state else None
    if isinstance(active_start, int) and 0 <= active_start < len(messages):
        return expand_archive_start_to_tool_call(messages, active_start)

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, ToolMessage) and message.name == "start_combat":
            return expand_archive_start_to_tool_call(messages, index)
        if isinstance(message, AIMessage) and any(tool_call.get("name") == "start_combat" for tool_call in message.tool_calls):
            return index
    return None


def trim_messages_to_token_budget(
    messages: list[BaseMessage],
    target_token_budget: int,
    *,
    preserve_start_index: int | None = None,
) -> list[BaseMessage]:
    """超过硬预算后只丢弃最旧前缀；战斗中会尽量保护开战后的完整工具链。"""
    if estimate_messages_tokens(messages) <= target_token_budget:
        return list(messages)

    if preserve_start_index is not None and 0 <= preserve_start_index < len(messages):
        preserve_start_index = align_message_window_start(messages, preserve_start_index)
        protected_messages = list(messages[preserve_start_index:])
        protected_tokens = estimate_messages_tokens(protected_messages)
        if protected_tokens >= target_token_budget:
            return prepend_compaction_message(messages[:preserve_start_index], protected_messages)

        prefix_budget = target_token_budget - protected_tokens
        prefix_messages = list(messages[:preserve_start_index])
        prefix_start = suffix_start_for_token_budget(prefix_messages, prefix_budget)
        return prepend_compaction_message(
            prefix_messages[:prefix_start],
            [*prefix_messages[prefix_start:], *protected_messages],
        )

    start_index = suffix_start_for_token_budget(messages, target_token_budget)
    return prepend_compaction_message(messages[:start_index], list(messages[start_index:]))


def suffix_start_for_token_budget(messages: list[BaseMessage], token_budget: int) -> int:
    """反向选择可放入预算的最长后缀，并避免窗口从悬空 ToolMessage 开始。"""
    if not messages:
        return 0

    total_tokens = 0
    start_index = len(messages) - 1
    for index in range(len(messages) - 1, -1, -1):
        message_tokens = estimate_message_tokens(messages[index])
        if total_tokens and total_tokens + message_tokens > token_budget:
            break
        total_tokens += message_tokens
        start_index = index

    return align_message_window_start(messages, start_index)


def align_message_window_start(messages: list[BaseMessage], start_index: int) -> int:
    """窗口起点若落在工具结果上，向前补齐触发它的 AI tool_call。"""
    while start_index > 0 and isinstance(messages[start_index], ToolMessage):
        start_index -= 1
    return start_index


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """用偏保守字符比估算 token，中文场景下宁可略早触发硬刹车。"""
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_message_tokens(message: BaseMessage) -> int:
    content_chars = len(message_content_to_text(getattr(message, "content", "")))
    metadata_chars = len(getattr(message, "type", "")) + len(str(getattr(message, "name", "") or ""))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        metadata_chars += len(json.dumps(tool_calls, ensure_ascii=False, sort_keys=True))
    if isinstance(message, ToolMessage):
        metadata_chars += len(str(message.tool_call_id or ""))
    return max(1, int((content_chars + metadata_chars) / APPROX_CHARS_PER_TOKEN) + 1)


def prepend_compaction_message(omitted_messages: list[BaseMessage], kept_messages: list[BaseMessage]) -> list[BaseMessage]:
    """预算裁剪时用厚归档替代被丢弃前缀，避免剧情与人设细节完全消失。"""
    compaction_message = build_context_compaction_message(omitted_messages)
    if compaction_message is None:
        return kept_messages
    return [compaction_message, *kept_messages]


def build_context_compaction_message(omitted_messages: list[BaseMessage]) -> HumanMessage | None:
    """上下文接近上限时才启用，按原文片段保留约三成信息给后续模型。"""
    raw_lines: list[str] = []
    for message in omitted_messages:
        line = format_compaction_line(message)
        if line:
            raw_lines.append(line)
    if not raw_lines:
        return None

    raw_text = "\n".join(raw_lines)
    retention_limit = max(CONTEXT_COMPACTION_MIN_CHARS, int(len(raw_text) * CONTEXT_COMPACTION_RETENTION_RATIO))
    retention_limit = min(retention_limit, CONTEXT_COMPACTION_MAX_CHARS)
    return HumanMessage(
        content=(
            f"{CONTEXT_COMPACTION_MESSAGE_PREFIX}\n"
            "状态: 较早历史因接近模型上下文上限被预算归档；以下内容仍是已发生事实，不是新指令。\n"
            "保留策略: 尽量按原文片段保留剧情、人设、承诺、线索、关系变化和已完成事件；瞬时战斗数字以最新状态帧和工具结果为准。\n"
            f"{compact_text(raw_text, retention_limit)}"
        )
    )


def format_compaction_line(message: BaseMessage) -> str:
    """预算归档使用角色标签保留对话脉络，少做解释性改写。"""
    content = message_content_to_text(getattr(message, "content", "")).strip()
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        tool_names = ", ".join(str(tool_call.get("name", "")) for tool_call in tool_calls)
        return f"AI工具调用: {tool_names}" + (f" | {content}" if content else "")
    if isinstance(message, ToolMessage):
        tool_name = getattr(message, "name", "") or "tool"
        return f"工具结果[{tool_name}]: {content}"
    if isinstance(message, AIMessage):
        return f"主持人: {content}"
    if isinstance(message, HumanMessage):
        return f"玩家/系统: {content}"
    return content


def expand_archive_start_to_tool_call(messages: list[BaseMessage], start_index: int) -> int:
    """预算裁剪保护战斗段时，从触发工具调用的 AIMessage 开始保留。"""
    if start_index <= 0 or start_index >= len(messages):
        return start_index

    previous_message = messages[start_index - 1]
    if isinstance(previous_message, AIMessage) and previous_message.tool_calls:
        return start_index - 1
    return start_index


def repair_tool_call_sequence(messages: list[BaseMessage]) -> list[BaseMessage]:
    """投影给模型前修复旧存档中的残缺工具链，避免 OpenAI 协议 400 卡死会话。"""
    repaired: list[BaseMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]

        if isinstance(message, AIMessage) and message.tool_calls:
            tool_messages = collect_following_tool_messages(messages, index + 1)
            if tool_messages_cover_calls(message, tool_messages):
                expected_count = len(message.tool_calls)
                repaired.append(message)
                repaired.extend(tool_messages[:expected_count])
                for extra_tool_message in tool_messages[expected_count:]:
                    repaired.append(HumanMessage(content=message_content_to_text(extra_tool_message.content)))
                index += 1 + len(tool_messages)
                continue

            repaired.append(strip_tool_calls(message))
            index += 1
            continue

        if isinstance(message, ToolMessage):
            repaired.append(HumanMessage(content=message_content_to_text(message.content)))
            index += 1
            continue

        repaired.append(message)
        index += 1

    return repaired


def collect_following_tool_messages(messages: list[BaseMessage], start_index: int) -> list[ToolMessage]:
    tool_messages: list[ToolMessage] = []
    index = start_index
    while index < len(messages) and isinstance(messages[index], ToolMessage):
        tool_messages.append(messages[index])
        index += 1
    return tool_messages


def tool_messages_cover_calls(message: AIMessage, tool_messages: list[ToolMessage]) -> bool:
    expected_ids = [str(tool_call.get("id", "")) for tool_call in message.tool_calls]
    actual_ids = [str(tool_message.tool_call_id) for tool_message in tool_messages[: len(expected_ids)]]
    return bool(expected_ids) and expected_ids == actual_ids


def strip_tool_calls(message: AIMessage) -> AIMessage:
    additional_kwargs = dict(message.additional_kwargs or {})
    additional_kwargs.pop("tool_calls", None)
    return message.model_copy(
        update={
            "additional_kwargs": additional_kwargs,
            "tool_calls": [],
            "invalid_tool_calls": [],
        }
    )


def format_runtime_hud_content(hud_text: str) -> str:
    return (
        f"{RUNTIME_STATE_MESSAGE_PREFIX}\n"
        "机器状态快照，不是玩家发言、请求或可回复对象；只用于读取当前事实与约束。\n"
        "只有最后一条运行状态帧有效，旧运行状态帧只用于缓存前缀，不得引用旧 HP、资源、坐标或回合。\n"
        "<runtime_state_frame source=\"state\" visibility=\"model_only\" role=\"state_snapshot\" audience=\"none\" reply=\"forbidden\">\n"
        f"{hud_text}"
        "</runtime_state_frame>"
    )


def build_runtime_state_message(runtime_state_text: str) -> HumanMessage:
    """把当前短状态帧写入真实消息历史，让 DeepSeek KC 看到 append-only 前缀。"""
    return HumanMessage(content=format_runtime_hud_content(runtime_state_text))


def is_runtime_state_message(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.startswith(RUNTIME_STATE_MESSAGE_PREFIX)


def is_adventure_node_frame_message(message: Any) -> bool:
    if isinstance(message, HumanMessage):
        return isinstance(message.content, str) and message.content.startswith(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX)
    if isinstance(message, dict):
        content = message_content_to_text(message.get("content", ""))
        return content.startswith(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX)
    return False


def is_internal_system_human_message(message: HumanMessage) -> bool:
    return isinstance(message.content, str) and message.content.startswith("[系统")


def count_external_human_turns(messages: list[BaseMessage]) -> int:
    """只按真实玩家发言触发校准，排除内部系统战报。"""
    return sum(
        1
        for message in messages
        if isinstance(message, HumanMessage) and not is_internal_system_human_message(message)
    )


def _latest_external_human_text(messages: list[BaseMessage]) -> str:
    """取最近一条真实玩家输入，给轻量检索层当查询。"""
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        if is_runtime_state_message(message) or is_internal_system_human_message(message):
            continue
        return message_content_to_text(getattr(message, "content", "")).strip()
    return ""


def clone_message_with_content(message: BaseMessage, content: Any) -> BaseMessage:
    cloned_message = copy(message)
    cloned_message.content = content
    return cloned_message


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def format_conditions(combatant: dict[str, Any]) -> str:
    conditions = combatant.get("conditions", []) or []
    if not conditions:
        return "无"
    return ", ".join(condition.get("name_cn") or condition.get("id", "?") for condition in conditions)


def format_resources(combatant: dict[str, Any]) -> str:
    """把资源压成一行，避免模型从旧对话里读取过期法术位。"""
    resources = combatant.get("resources", {}) or {}
    caps = combatant.get("resource_caps", {}) or {}
    if not resources:
        return "无"
    parts = []
    for key in sorted(resources):
        cap = caps.get(key)
        value = resources.get(key)
        parts.append(f"{key}={value}/{cap}" if cap is not None else f"{key}={value}")
    return ", ".join(parts)


def format_magic(combatant: dict[str, Any]) -> str:
    """把施法相关事实压成稳定短句，避免友方法术资源被旧消息覆盖。"""
    known_spells = combatant.get("known_spells", []) or []
    known_cantrips = combatant.get("known_cantrips", []) or []
    parts: list[str] = []
    if known_spells:
        parts.append("spells=" + ",".join(str(spell_id) for spell_id in known_spells))
    if known_cantrips:
        parts.append("cantrips=" + ",".join(str(spell_id) for spell_id in known_cantrips))
    return "; ".join(parts) if parts else "无"


def format_reaction_status(combatant: dict[str, Any]) -> str:
    """明确反应是否可用，并列出可见的反应法术/动作。"""
    status = "可用" if combatant.get("reaction_available", False) else "已用/不可用"
    reaction_labels: list[str] = []

    try:
        from app.spells import get_spell_def
        for spell_id in combatant.get("known_spells", []) or []:
            spell_def = get_spell_def(str(spell_id))
            if spell_def and spell_def.get("casting_time") == "reaction":
                reaction_labels.append(str(spell_id))
    except Exception:
        reaction_labels = []

    for action in combatant.get("actions", []) or []:
        if action.get("action_type") == "reaction" or action.get("kind") == "reaction":
            reaction_labels.append(f"{action.get('name', '?')}({action.get('id', '?')})")

    if reaction_labels:
        return f"{status}: " + ",".join(dict.fromkeys(reaction_labels))
    return status


def format_behavior_profile(combatant: dict[str, Any]) -> str:
    """友方行为偏好只作为决策线索，不替代当前战场事实。"""
    return str(combatant.get("behavior_profile") or "无")


def format_death_save_status(combatant: dict[str, Any]) -> str:
    """高亮死亡豁免状态，让 0 HP 玩家不会被误判为游戏结束。"""
    if combatant.get("is_dead"):
        return "dead"
    if combatant.get("is_stable"):
        return "stable"
    successes = int(combatant.get("death_save_successes", 0) or 0)
    failures = int(combatant.get("death_save_failures", 0) or 0)
    if combatant.get("hp", 0) <= 0 or successes or failures:
        return f"{successes}成功/{failures}失败"
    return "无"


def format_player_priority_summary(player: dict[str, Any]) -> str:
    """把当前资源和濒死状态放在 JSON 前，降低旧消息的干扰权重。"""
    return (
        f"HP:{player.get('hp')}/{player.get('max_hp')} "
        f"AC:{compute_ac(player)} "
        f"coins:{format_coins(player)} "
        f"resources:[{format_resources(player)}] "
        f"items:[{format_consumables(player)}] "
        f"death_saves:{format_death_save_status(player)}"
    )


def format_coins(unit: dict[str, Any]) -> str:
    """短状态帧展示金币，避免购物工具靠旧对话猜余额。"""
    coins = unit.get("coins", {}) or {}
    if not coins:
        return "无"
    return ",".join(f"{key}={value}" for key, value in sorted(coins.items()))


def format_consumables(unit: dict[str, Any]) -> str:
    """只暴露可主动使用的消耗品数量，避免完整财宝描述污染短帧。"""
    items: list[str] = []
    for item in unit.get("inventory", []) or []:
        if item.get("type") != "potion":
            continue
        quantity = int(item.get("quantity", 0) or 0)
        if quantity <= 0:
            continue
        items.append(f"{item.get('id', '?')}x{quantity}")
    return ",".join(items) if items else "无"


def format_attacks(combatant: dict[str, Any]) -> str:
    attacks = combatant.get("attacks", []) or []
    if not attacks:
        return "无"
    return ", ".join(attack.get("name", "?") for attack in attacks)


def format_actions(combatant: dict[str, Any]) -> str:
    """战斗上下文优先展示结构化动作，让模型调用 action_id 而不是猜攻击名。"""
    actions = combatant.get("actions", []) or []
    if actions:
        return ", ".join(
            f"{action.get('name', '?')}({action.get('id', '?')}, {action.get('kind', '?')})"
            for action in actions
        )
    return format_attacks(combatant)


def format_space_summary(space: dict[str, Any]) -> str:
    """把空间状态压缩成 HUD 可读文本，避免模型吞下整份 JSON。"""
    if not space or not space.get("maps"):
        return "当前没有平面地图。探索或战斗若涉及位置、距离、范围、入场或移动，应先用 manage_space 建立地图并放置相关单位。"

    active_map_id = space.get("active_map_id", "")
    maps = space.get("maps", {}) or {}
    placements = space.get("placements", {}) or {}
    active_map = maps.get(active_map_id, {})

    lines = [
        (
            f"当前地图: {active_map.get('name', active_map_id)} [ID:{active_map_id}] "
            f"尺寸:{active_map.get('width', '?')}x{active_map.get('height', '?')}尺 "
            f"网格:{active_map.get('grid_size', '?')}尺"
        )
    ]
    unit_lines: list[str] = []
    for unit_id, placement in placements.items():
        if placement.get("map_id") != active_map_id:
            continue
        position = placement.get("position", {}) or {}
        unit_lines.append(
            f"  {unit_id}: ({position.get('x', '?')}, {position.get('y', '?')}) "
            f"朝向:{placement.get('facing_deg', 0)}"
        )
    if unit_lines:
        lines.append("当前地图单位坐标:")
        lines.extend(unit_lines)
    else:
        lines.append("当前地图暂无已放置单位。")
    return "\n".join(lines)


def active_space_map_summary(space: dict[str, Any]) -> str:
    """短状态帧只提示地图存在与单位数量，坐标细节交给空间工具。"""
    if not space or not space.get("maps"):
        return ""

    active_map_id = space.get("active_map_id", "")
    maps = space.get("maps", {}) or {}
    placements = space.get("placements", {}) or {}
    active_map = maps.get(active_map_id, {})
    placed_count = sum(1 for placement in placements.values() if placement.get("map_id") == active_map_id)
    return (
        f"空间: 当前地图 {active_map.get('name', active_map_id)} [ID:{active_map_id}]，"
        f"已放置单位 {placed_count} 个；距离、范围和坐标以空间工具返回为准。"
    )


def format_narrative_ally_snapshot(uid: str, unit: dict[str, Any]) -> str:
    """探索态暴露友方关键资源，避免模型忽略可用治疗、施法和职业动作。"""
    class_actions = available_class_actions(unit)
    parts = [
        f"{unit.get('name', uid)}[ID:{uid}",
        f"HP:{unit.get('hp')}/{unit.get('max_hp')}",
        f"AC:{compute_ac(unit)}",
        f"resources:{format_resources(unit)}",
        f"items:{format_consumables(unit)}",
        f"magic:{format_magic(unit)}",
    ]
    if class_actions:
        parts.append("class_actions:" + ",".join(class_actions))
    actions = format_actions(unit)
    if actions != "无":
        parts.append("actions:" + actions)
    return ", ".join(parts) + "]"


def format_combat_side_snapshot(participants: dict[str, Any], side: str) -> str:
    """短战斗帧按阵营列出关键战斗状态，避免完整参战者 JSON 干扰缓存。"""
    items: list[str] = []
    for uid, combatant in participants.items():
        if combatant.get("side") != side:
            continue
        items.append(
            f"{combatant.get('name', uid)}[ID:{uid}, HP:{combatant.get('hp')}/{combatant.get('max_hp')}, "
            f"AC:{compute_ac(combatant)}, resources:{format_resources(combatant)}, "
            f"items:{format_consumables(combatant)}, "
            f"conditions:{format_conditions(combatant)}, magic:{format_magic(combatant)}, actions:{format_actions(combatant)}]"
        )
    return "；".join(items)


def format_scene_magic_snapshots(units: dict[str, Any]) -> str:
    """探索期也要暴露可施法单位；法术是行动选项，不应藏到完整 inspect 之后。"""
    items: list[str] = []
    for uid, unit in units.items():
        magic = format_magic(unit)
        if magic == "无":
            continue
        items.append(
            f"{unit.get('name', uid)}[ID:{uid}, resources:{format_resources(unit)}, magic:{magic}]"
        )
    return "；".join(items[:6])


def format_adventure_summary(
    adventure: dict[str, Any] | None,
    *,
    visible_clue_ids: list[str] | None = None,
) -> str:
    """把冒险状态压缩进 HUD，避免模型每轮都重读完整节点。"""
    adventure_dict = normalize_adventure_state(adventure or {})
    try:
        node = get_adventure_store().get_node(adventure_dict["active_node_id"])
        node_title = node.title
        node_summary = node.dm_summary
        exit_labels = [f"{item.id}: {item.label} -> {item.next_node_id}" for item in node.exits]
        source_pages = _node_source_pages(node)
    except Exception:
        node_title = adventure_dict["active_node_id"]
        node_summary = ""
        exit_labels = []
        source_pages = "未知"

    lines = [
        f"模组: {adventure_dict['module_id']}",
        f"当前节点: {node_title} [ID:{adventure_dict['active_node_id']}]",
        f"页码: {source_pages}",
        f"节点摘要: {_sanitize_adventure_summary(node_summary)}",
        "已知线索: "
        + format_known_clue_window(
            _visible_adventure_clue_window(adventure_dict, visible_clue_ids=visible_clue_ids),
            total_count=len(adventure_dict.get("known_clue_ids") or []),
        ),
        f"已完成事件: {adventure_dict.get('completed_event_ids') or []}",
        f"已解锁节点: {adventure_dict.get('unlocked_node_ids') or []}",
    ]
    if len(adventure_dict.get("breadcrumb_node_ids", [])) > 1:
        lines.append(f"路径回溯: {recent_breadcrumb_ids(adventure_dict, limit=6)}")
    if adventure_dict.get("deferred_node_ids"):
        lines.append(f"待回访节点: {(adventure_dict.get('deferred_node_ids') or [])[:6]}")
    pending_rewards = _format_pending_reward_grants(adventure_dict.get("pending_reward_grants"))
    if pending_rewards:
        lines.append(f"待发放奖励: {pending_rewards}")
        lines.append("当前必须先调用 claim_adventure_reward 领取待发放奖励，成功后再继续叙事。")
    if exit_labels:
        lines.append("当前节点出口: " + "；".join(exit_labels))
    lines.append("节点奖励先进入待发放队列；发放时使用剧情奖励专用工具，不要自行发放或重复发放。")
    lines.append("每次探索回应都必须贴合当前节点；若本轮涉及剧情事实、线索或推进，先依据当前节点与路径回溯自然演绎，不要自造新地点。")
    return "\n".join(lines)


def _visible_adventure_clue_window(
    adventure_dict: dict[str, Any],
    *,
    visible_clue_ids: list[str] | None = None,
    query: str = "",
) -> list[str]:
    """优先使用 Director 指定窗口；否则按当前节点和玩家意图投影。"""
    known = set(adventure_dict.get("known_clue_ids") or [])
    directed = [clue_id for clue_id in visible_clue_ids or [] if clue_id in known]
    if directed:
        return directed[:6]
    return project_known_clue_window(
        adventure_dict,
        current_node_id=adventure_dict["active_node_id"],
        query=query,
    )


def summarize_tool_message(message: ToolMessage) -> str:
    tool_name = getattr(message, "name", "") or "tool"
    raw_text = message_content_to_text(message.content).strip()

    if has_hp_resolution_lines(raw_text):
        return summarize_state_resolution_tool(tool_name, raw_text)

    if tool_name == "load_adventure_node":
        return summarize_adventure_node_tool(raw_text)
    if tool_name == "search_adventure_nodes":
        return f"[工具:{tool_name}] {compact_text(raw_text, 3000)}"
    if tool_name == "inspect_adventure_state" and raw_text.startswith("# 冒险模组主持技能"):
        return f"[工具:{tool_name}] {compact_text(raw_text, 800)}"
    if tool_name in {"inspect_adventure_state", "switch_adventure_node", "reveal_adventure_clue", "mark_adventure_event", "advance_adventure"}:
        return f"[工具:{tool_name}] {compact_text(raw_text, 1800)}"

    if (
        tool_name in {"consult_rules_handbook", "load_skill"}
        or (tool_name == "modify_character_state" and raw_text.startswith("# 角色状态调整技能"))
        or (tool_name == "modify_character_state" and raw_text.startswith("# 角色成长与子职技能"))
        or (tool_name == "manage_space" and raw_text.startswith("# 平面空间管理技能"))
    ):
        # 规则/技能结果若被过度压缩，会让下一轮模型看不到关键依据而回退到记忆作答。
        return f"[工具:{tool_name}] {compact_text(raw_text, 800)}"

    if tool_name == "inspect_unit":
        return f"[工具:{tool_name}] {compact_text(raw_text, 4000)}"

    if tool_name == "request_dice_roll":
        try:
            roll_data = json.loads(raw_text)
        except json.JSONDecodeError:
            roll_data = None
        if isinstance(roll_data, dict):
            raw_roll = roll_data.get("raw_roll", "?")
            final_total = roll_data.get("final_total", raw_roll)
            return f"[工具:{tool_name}] 掷骰结果 raw={raw_roll} total={final_total}"

    if tool_name == "cast_spell":
        # 多目标法术（如魔法飞弹）每个目标的 HP 变化都在工具返回里，不能只保留前三行。
        return f"[工具:{tool_name}] {compact_text(raw_text, 2000)}"

    if tool_name == "manage_inventory":
        # 价目表是后续购物决策依据，不能被通用两行摘要折叠掉。
        return f"[工具:{tool_name}] {compact_text(raw_text, 3000)}"

    summary_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    summary = " | ".join(summary_lines[:2])
    if not summary:
        summary = raw_text[:180] or "工具已执行。"
    if len(summary) > 180:
        summary = summary[:177] + "..."
    return f"[工具:{tool_name}] {summary}"


def has_hp_resolution_lines(raw_text: str) -> bool:
    """识别所有工具返回中的 HP 结算行，而不限于攻击工具。"""
    return any(" HP:" in line and ("→" in line or "->" in line) for line in raw_text.splitlines())


def summarize_state_resolution_tool(tool_name: str, raw_text: str) -> str:
    """保留所有状态写回行，避免模型拿 HUD 当前值重复结算。"""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return f"[工具:{tool_name}] 工具已执行。"

    important_lines: list[str] = []
    for line in lines:
        if (
            not important_lines
            or line.startswith("命中骰:")
            or line.startswith("伤害骰:")
            or " HP:" in line
            or "倒下" in line
            or "死亡豁免" in line
            or "恢复" in line
            or "治疗" in line
            or "剩余" in line
            or "资源" in line
            or "法术位" in line
            or "未命中" in line
            or "严重失误" in line
            or line.startswith("[")
            or line.startswith("当前行动者")
        ):
            important_lines.append(line)

    important_lines.append("结算状态：以上 HP 变化已经由工具写回；后续叙事只能复述结果，不得再次扣减、治疗或改写 HP。")
    return f"[工具:{tool_name}] {compact_text(' | '.join(dict.fromkeys(important_lines)), 1600)}"


def summarize_adventure_node_tool(raw_text: str) -> str:
    """冒险节点是剧情事实源，保留给模型足够原文与结构化主持材料。"""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return f"[工具:load_adventure_node] {compact_text(raw_text, 8000)}"

    node = payload.get("node", {}) if isinstance(payload, dict) else {}
    projected = {
        "node": {
            "id": node.get("id"),
            "title": node.get("title"),
            "kind": node.get("kind"),
            "source_pages": node.get("source_pages"),
            "source_refs": node.get("source_refs", []),
            "source_excerpt": node.get("source_excerpt"),
            "source_text": compact_text(str(node.get("source_text", "")), 6000),
            "subsections": node.get("subsections", []),
            "dm_summary": node.get("dm_summary"),
            "player_visible_intro": node.get("player_visible_intro"),
            "scene_beats": node.get("scene_beats", []),
            "rules_notes": node.get("rules_notes", []),
            "npc_reveals": node.get("npc_reveals", []),
            "secrets": node.get("secrets", []),
            "checks": node.get("checks", []),
            "encounters": node.get("encounters", []),
            "rewards": node.get("rewards", []),
            "clues": node.get("clues", []),
            "events": node.get("events", []),
            "fallbacks": node.get("fallbacks", []),
            "dm_guidance": node.get("dm_guidance", {}),
            "rules_overrides": node.get("rules_overrides", []),
        },
        "progression_rule": payload.get(
            "progression_rule",
            "剧情推进出口只看顶层 available_exits；available_exits 非空且 available=true 时即可用对应 id 调用 advance。",
        ),
        "available_exits": payload.get("available_exits", []),
        "adventure_state": payload.get("adventure_state", {}),
    }
    if node.get("candidate_exits"):
        projected["node"]["candidate_exits"] = node["candidate_exits"]
    return "[工具:load_adventure_node] " + compact_text(json.dumps(projected, ensure_ascii=False), 10000)


def compact_text(text: str, limit: int) -> str:
    """压缩空白并按字符上限截断，保留工具消息的主要事实。"""
    compact = " ".join(text.split())
    if len(compact) > limit:
        return compact[: limit - 3] + "..."
    return compact


def summarize_system_message(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    head = lines[0] if lines else "[系统]"
    body = " | ".join(lines[1:3])
    if body:
        return f"{head} {body}"
    return head


def dump_mapping_state(value: Any) -> dict[str, Any]:
    if not value or not hasattr(value, "items"):
        return {}
    return {
        key: item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for key, item in value.items()
    }


def state_value_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [state_value_to_dict(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "items"):
        return {key: state_value_to_dict(item) for key, item in value.items()}
    return value
