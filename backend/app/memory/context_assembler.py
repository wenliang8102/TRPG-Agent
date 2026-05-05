"""上下文装配器。"""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.graph.state import GraphState
from app.services.tools._helpers import compute_ac


COMBAT_ARCHIVE_MESSAGE_PREFIX = "[系统:战斗归档]"


class ExternalContextProvider(Protocol):
    """为未来的外部 RAG 能力预留注入口，当前默认不返回任何片段。"""

    def get_context_blocks(self, *, state: GraphState, mode: str) -> list[str]: ...


class NoopExternalContextProvider:
    def get_context_blocks(self, *, state: GraphState, mode: str) -> list[str]:
        return []


@dataclass(slots=True)
class AssembledContext:
    system_prompt: str
    hud_text: str
    model_input_messages: list[BaseMessage]


class ContextAssembler:
    """统一拼装系统提示、HUD 和模型可见消息窗口。"""

    def __init__(self, external_context_provider: ExternalContextProvider | None = None) -> None:
        self._external_context_provider = external_context_provider or NoopExternalContextProvider()

    def assemble(self, state: GraphState, mode: str, *, base_system_prompt: str) -> AssembledContext:
        """把图状态投影为一次模型调用所需的完整上下文。"""
        hud_text = self.build_hud_text(state)
        return AssembledContext(
            system_prompt=self.build_system_prompt(state, mode, base_system_prompt),
            hud_text=hud_text,
            model_input_messages=self.build_model_input_messages(state, mode, hud_text),
        )

    def build_system_prompt(self, state: GraphState, mode: str, base_system_prompt: str) -> str:
        system_prompt = base_system_prompt

        episodic_context = [item.strip() for item in state.get("episodic_context", []) if isinstance(item, str) and item.strip()]
        if episodic_context:
            system_prompt += "\n\n[近期情节记忆]\n" + "\n".join(f"- {item}" for item in episodic_context)
        elif summary := state.get("conversation_summary"):
            system_prompt += f"\n\n[前情提要（必须铭记的游戏大纲）]\n{summary}"

        if mode == COMBAT_AGENT_MODE:
            combat_brief = self._build_combat_brief(state)
            if combat_brief:
                system_prompt += f"\n\n[战斗简报]\n{combat_brief}"

            turn_directive = self._build_combat_turn_directive(state)
            if turn_directive:
                system_prompt += f"\n\n[当前回合指令]\n{turn_directive}"

        external_blocks = self._external_context_provider.get_context_blocks(state=state, mode=mode)
        if external_blocks:
            system_prompt += "\n\n[扩展上下文]\n" + "\n\n".join(block for block in external_blocks if block)

        return system_prompt

    def build_hud_text(self, state: GraphState) -> str:
        sections: list[str] = []

        player_dict = state_value_to_dict(state.get("player"))
        if player_dict:
            sections.append("[当前玩家状态]\n" + json.dumps(player_dict, ensure_ascii=False, indent=2))
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
                    f"conditions=[{format_conditions(combatant)}] actions=[{actions_desc}]{marker}"
                )
            sections.append("[当前战斗状态]\n" + "\n".join(combat_lines))

        scene_data = dump_mapping_state(state.get("scene_units"))
        if scene_data:
            scene_lines = [
                f"  {uid}: {unit.get('name', uid)} (side={unit.get('side')}, HP:{unit.get('hp')}/{unit.get('max_hp')})"
                for uid, unit in scene_data.items()
            ]
            sections.append("[场景单位池（可用 start_combat 指定参战）]\n" + "\n".join(scene_lines))

        space_data = state_value_to_dict(state.get("space"))
        sections.append("[当前平面空间]\n" + format_space_summary(space_data))

        dead_data = dump_mapping_state(state.get("dead_units"))
        if dead_data:
            dead_lines = [f"  {uid}: {unit.get('name', uid)}" for uid, unit in dead_data.items()]
            sections.append("[死亡单位档案]\n" + "\n".join(dead_lines))

        return "\n\n=== 状态快照 ===\n" + "\n\n".join(sections) + "\n===========================\n"

    def build_model_input_messages(self, state: GraphState, mode: str, hud_text: str) -> list[BaseMessage]:
        source_messages = collapse_archived_combat_messages(
            list(state.get("messages", [])),
            state.get("combat_archives", []),
        )
        trimmed_messages = trim_model_messages(source_messages, mode)
        projected_messages: list[BaseMessage] = []

        for message in trimmed_messages:
            if isinstance(message, ToolMessage):
                projected_messages.append(clone_message_with_content(message, summarize_tool_message(message)))
                continue

            if isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.startswith("[系统:"):
                if message.content.startswith(COMBAT_ARCHIVE_MESSAGE_PREFIX):
                    projected_messages.append(message)
                    continue
                projected_messages.append(clone_message_with_content(message, summarize_system_message(message.content)))
                continue

            projected_messages.append(message)

        repaired_messages = repair_tool_call_sequence(projected_messages)
        return insert_runtime_hud_message(repaired_messages, hud_text)

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
        enemy_side: list[str] = []
        for uid, combatant in participants.items():
            display_ac = compute_ac(combatant) if isinstance(combatant, dict) else combatant.get('ac')
            status = (
                f"{combatant.get('name', uid)}[HP:{combatant.get('hp')}/{combatant.get('max_hp')}, "
                f"AC:{display_ac}, conditions:{format_conditions(combatant)}, "
                f"actions:{format_actions(combatant)}]"
            )
            if combatant.get("side") == "player":
                player_side.append(status)
            else:
                enemy_side.append(status)

        if player_side:
            lines.append("玩家侧: " + "；".join(player_side))
        if enemy_side:
            lines.append("对立侧: " + "；".join(enemy_side))

        return "\n".join(lines)

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
            return (
                f"当前是玩家单位 {current_name} [ID:{current_id}] 的回合。"
                "根据玩家最新意图调用合适工具；若本回合已无合理动作，调用 next_turn 结束当前行动者回合。"
            )

        return (
            f"当前是怪物/NPC {current_name} [ID:{current_id}] 的回合。"
            "你必须立刻为其选择一个可执行动作并调用工具，不要等待用户继续发话；"
            "若需要接近目标，优先调用 manage_space(action=\"approach_unit\") 一步靠近到合适距离，"
            "不要反复测距或手算坐标；"
            "不要只用文字宣告换人；当你判断该单位本回合可做且应做的事情都完成后，"
            "必须调用 next_turn 结束当前行动者回合。"
        )


def trim_model_messages(messages: list[BaseMessage], mode: str) -> list[BaseMessage]:
    keep_count = 50 if mode == NARRATIVE_AGENT_MODE else 32
    if len(messages) <= keep_count:
        return list(messages)

    start_index = len(messages) - keep_count
    while start_index > 0 and isinstance(messages[start_index], ToolMessage):
        start_index -= 1

    return list(messages[start_index:])


def collapse_archived_combat_messages(messages: list[BaseMessage], combat_archives: list[dict[str, Any]] | None) -> list[BaseMessage]:
    """中文注释：战后只把整段战斗投影成一条摘要，避免后续回合继续吞下整串战报。"""
    normalized_archives = normalize_combat_archives(combat_archives, len(messages))
    if not normalized_archives:
        return list(messages)

    collapsed: list[BaseMessage] = []
    cursor = 0
    for archive in normalized_archives:
        start_index = expand_archive_start_to_tool_call(messages, archive["start_index"])
        end_index = archive["end_index"]

        if start_index < cursor:
            continue

        collapsed.extend(messages[cursor:start_index])
        summary = archive["summary"]
        if summary:
            collapsed.append(HumanMessage(content=f"{COMBAT_ARCHIVE_MESSAGE_PREFIX}\n{summary}"))
        else:
            collapsed.extend(messages[start_index:end_index + 1])
        cursor = end_index + 1

    collapsed.extend(messages[cursor:])
    return collapsed


def expand_archive_start_to_tool_call(messages: list[BaseMessage], start_index: int) -> int:
    """战斗归档必须连同触发工具的 AIMessage 一起折叠，否则会留下悬空 tool_calls。"""
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
                repaired.append(message)
                repaired.extend(tool_messages)
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


def normalize_combat_archives(combat_archives: list[dict[str, Any]] | None, message_count: int) -> list[dict[str, Any]]:
    if not combat_archives or message_count <= 0:
        return []

    normalized: list[dict[str, Any]] = []
    for archive in combat_archives:
        if hasattr(archive, "model_dump"):
            archive = archive.model_dump()
        elif hasattr(archive, "items"):
            archive = dict(archive)
        else:
            continue

        start_index = archive.get("start_index")
        end_index = archive.get("end_index")
        if not isinstance(start_index, int) or not isinstance(end_index, int):
            continue
        if start_index < 0 or end_index < start_index or start_index >= message_count:
            continue

        normalized.append(
            {
                "summary": str(archive.get("summary", "")).strip(),
                "start_index": start_index,
                "end_index": min(end_index, message_count - 1),
            }
        )

    normalized.sort(key=lambda item: (item["start_index"], item["end_index"]))
    return normalized


# 将 HUD 作为临近当前轮次的系统消息注入，避免被模型误读成玩家发言。
def insert_runtime_hud_message(messages: list[BaseMessage], hud_text: str) -> list[BaseMessage]:
    hud_message = SystemMessage(content=format_runtime_hud_content(hud_text))
    if not messages:
        return [hud_message]

    projected_messages = list(messages)
    latest_message = projected_messages[-1]
    if isinstance(latest_message, HumanMessage) and not is_internal_system_human_message(latest_message):
        return [*projected_messages[:-1], hud_message, latest_message]

    tool_exchange_start = trailing_tool_exchange_start_index(projected_messages)
    if tool_exchange_start is not None:
        return [
            *projected_messages[:tool_exchange_start],
            hud_message,
            *projected_messages[tool_exchange_start:],
        ]

    return [*projected_messages, hud_message]


def trailing_tool_exchange_start_index(messages: list[BaseMessage]) -> int | None:
    """工具回填后保持 AI tool_calls 与 ToolMessage 相邻，避免续跑模型误判工具链已结束。"""
    if not messages or not isinstance(messages[-1], ToolMessage):
        return None

    first_tool_index = len(messages) - 1
    while first_tool_index > 0 and isinstance(messages[first_tool_index - 1], ToolMessage):
        first_tool_index -= 1

    ai_index = first_tool_index - 1
    if ai_index >= 0 and isinstance(messages[ai_index], AIMessage) and messages[ai_index].tool_calls:
        return ai_index
    return None


def format_runtime_hud_content(hud_text: str) -> str:
    return (
        "<runtime_state source=\"hud\" visibility=\"model_only\" role=\"state_snapshot\" audience=\"none\">\n"
        f"{hud_text}"
        "</runtime_state>"
    )


def is_internal_system_human_message(message: HumanMessage) -> bool:
    return isinstance(message.content, str) and message.content.startswith("[系统")


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


def summarize_tool_message(message: ToolMessage) -> str:
    tool_name = getattr(message, "name", "") or "tool"
    raw_text = message_content_to_text(message.content).strip()

    if (
        tool_name in {"consult_rules_handbook", "load_skill"}
        or (tool_name == "modify_character_state" and raw_text.startswith("# 角色状态调整技能"))
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

    summary_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    max_lines = 3 if tool_name in {"attack_action", "use_monster_action", "cast_spell"} else 2
    summary = " | ".join(summary_lines[:max_lines])
    if not summary:
        summary = raw_text[:180] or "工具已执行。"
    if len(summary) > 180:
        summary = summary[:177] + "..."
    return f"[工具:{tool_name}] {summary}"


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
