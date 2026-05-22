"""战斗工具链 — 生成怪物、开始/结束战斗、攻击、回合推进"""

from __future__ import annotations

import json
from typing import Annotated, Literal

import d20
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.allies.profiles import get_ally_profile
from app.adventures.navigation import normalize_adventure_state
from app.adventures.store import get_adventure_store
from app.calculation.bestiary import spawn_combatants
from app.calculation.combat_xp import grant_combat_xp
from app.services.skills import load_skill_content
from app.space.geometry import build_space_state
from app.services.tools._helpers import (
    advance_turn,
    apply_hp_change,
    apply_attack_damage,
    build_attack_roll_event_payload,
    build_pending_reaction_state,
    canonical_combatant_id,
    canonicalize_player_space,
    clear_player_combat_fields,
    choose_attack,
    consume_action_resource,
    available_attack_names,
    get_all_combatants,
    get_combatant,
    has_action_resource,
    prepare_player_for_combat,
    prepare_character_for_combat,
    resolve_player_reference_id,
    roll_attack_hit,
    tracks_death_saves,
    validate_attack_distance,
)
from app.services.tools.reactions import get_available_reactions
from app.services.tools.spell_tools import apply_automatic_hit_reaction


def _message_count(state: dict | None) -> int:
    messages = state.get("messages") or [] if state else []
    return len(messages)


def _active_combat_start_index(state: dict | None) -> int:
    """记录活跃战斗起点，便于极端长上下文裁剪时保护完整战斗工具链。"""
    messages = state.get("messages") or [] if state else []
    if messages and getattr(messages[-1], "tool_calls", None):
        return len(messages) - 1
    return len(messages)


def _remove_space_units(space_raw: dict | None, unit_ids: list[str]) -> dict | None:
    """战斗收尾时把尸体从空间落点里真正移除，避免地图上只剩“摆角落”的假清理。"""
    if not space_raw:
        return None

    space = build_space_state(space_raw)
    for unit_id in unit_ids:
        space.placements.pop(unit_id, None)
    return space.model_dump()


def _adventure_after_combat_update(state: dict | None) -> tuple[dict | None, list[str]]:
    """战斗结束后只记录当前遭遇节点的确定事件，把路线推进交还给后台导航器。"""
    adventure_raw = state.get("adventure") if state else None
    if not adventure_raw:
        return None, []

    adventure = normalize_adventure_state(adventure_raw)
    node = get_adventure_store().get_node(adventure["active_node_id"])
    recorded: list[str] = []
    if node.kind == "encounter":
        for event_id in node.events:
            if event_id not in adventure["completed_event_ids"]:
                adventure["completed_event_ids"].append(event_id)
                recorded.append(event_id)
    return adventure, recorded


def _validate_combat_space(state: dict, unit_ids: list[str]) -> str | None:
    """战斗必须绑定客观地图，否则距离、移动和范围规则会失去事实来源。"""
    space = build_space_state(state.get("space"))
    if not space.maps or not space.active_map_id or space.active_map_id not in space.maps:
        return "无法开始战斗：当前没有可用平面地图。请先用 manage_space 创建或切换地图，并放置参战单位。"

    missing = [unit_id for unit_id in unit_ids if unit_id not in space.placements]
    if missing:
        return f"无法开始战斗：以下参战单位尚未放置到当前平面地图: {', '.join(missing)}。请先用 manage_space 放置单位。"

    wrong_map = [unit_id for unit_id in unit_ids if space.placements[unit_id].map_id != space.active_map_id]
    if wrong_map:
        active_map = space.maps[space.active_map_id]
        return f"无法开始战斗：以下参战单位不在当前地图 {active_map.name} [ID:{space.active_map_id}]: {', '.join(wrong_map)}。请先切换地图或重新放置单位。"

    return None


def _resolve_surprised_ids(raw_ids: list[str], all_units: dict[str, dict], player_dict: dict | None) -> tuple[set[str], list[str]]:
    """新版突袭只标记先攻劣势目标，避免再引入跳过回合的旧状态。"""
    surprised: set[str] = set()
    missing: list[str] = []
    for raw_id in raw_ids:
        unit_id = resolve_player_reference_id(player_dict, str(raw_id))
        if unit_id in all_units:
            surprised.add(unit_id)
        else:
              missing.append(str(raw_id))
    return surprised, missing


def _normalize_string_list_arg(field_name: str, value: list[str] | str | None, tool_call_id: str | None) -> list[str] | Command:
    """兼容模型把字符串列表参数误序列化为 JSON 字符串的工具调用形态。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return Command(update={"messages": [
            ToolMessage(content=f"{field_name} 必须是字符串列表或可解析为字符串列表的 JSON 字符串。", tool_call_id=tool_call_id)
        ]})
    if not isinstance(parsed, list):
        return Command(update={"messages": [
            ToolMessage(content=f"{field_name} 必须是字符串列表，不能是 {type(parsed).__name__}。", tool_call_id=tool_call_id)
        ]})
    return [str(item) for item in parsed]


def _roll_initiative(unit: dict, *, surprised: bool) -> tuple[int, str]:
    """先攻骰统一入口；新版突袭让目标先攻劣势，而不是失去首回合。"""
    dex_mod = unit.get("modifiers", {}).get("dex", 0)
    expr = f"2d20kl1+{dex_mod}" if surprised else f"1d20+{dex_mod}"
    result = d20.roll(expr)
    return result.total, str(result)


def _execute_start_combat(
    combatant_ids: list[str],
    surprised_ids: list[str] | str | None,
    *,
    state: dict,
    tool_call_id: str | None = None,
) -> Command | str:
    """开战唯一实现；工具入口和 workflow 节点共用，避免两套先攻规则漂移。"""
    surprised_ids = _normalize_string_list_arg("surprised_ids", surprised_ids, tool_call_id)
    if isinstance(surprised_ids, Command):
        return surprised_ids

    scene_units: dict = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    else:
        scene_raw = {}

    if not combatant_ids and not scene_raw:
        return "场景中没有任何单位。请先使用 spawn_monsters 或 spawn_ally 生成参战单位。"

    # 玩家自动入场 — 直接在 player_dict 上叠加战斗字段，不再复制到 participants
    player_raw = state.get("player")
    player_dict: dict | None = None
    if player_raw:
        player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
        prepare_player_for_combat(player_dict)

    player_id = str(player_dict.get("id")) if player_dict else ""
    participants: dict[str, dict] = {}
    missing: list[str] = []
    for uid in combatant_ids:
        if uid == player_id:
            continue
        unit = scene_raw.get(uid)
        if unit:
            participants[uid] = unit
        else:
            missing.append(uid)

    if missing:
        available_ids = [unit_id for unit_id in scene_raw.keys()]
        if player_id:
            available_ids.append(player_id)
        available = ", ".join(available_ids) or "无"
        return f"找不到以下单位: {', '.join(missing)}。场景中可用单位: {available}"

    if not participants and not player_dict:
        return "没有参战者，请先生成怪物或加载角色卡。"

    for uid, unit in list(participants.items()):
        if unit.get("side") == "ally" or unit.get("unit_kind") == "character":
            prepare_character_for_combat(unit, side=unit.get("side", "ally"), fallback_id=uid)
            participants[uid] = unit

    # 为所有参战单位投先攻（含玩家）
    all_units: dict[str, dict] = dict(participants)
    if player_dict:
        all_units[player_dict["id"]] = player_dict

    state = {**state, "space": canonicalize_player_space(state.get("space"), player_dict)}
    if space_error := _validate_combat_space(state, list(all_units)):
        return Command(update={"messages": [ToolMessage(content=space_error, tool_call_id=tool_call_id)]})

    surprised_set, surprise_missing = _resolve_surprised_ids(surprised_ids or [], all_units, player_dict)
    if surprise_missing:
        available = ", ".join(all_units.keys()) or "无"
        return Command(update={"messages": [
            ToolMessage(
                content=f"无法开始战斗：找不到被突袭单位 {', '.join(surprise_missing)}。可用单位: {available}",
                tool_call_id=tool_call_id,
            )
        ]})

    initiative_list: list[tuple[str, int, str, bool]] = []
    for uid, p in all_units.items():
        surprised = uid in surprised_set
        init_total, roll_text = _roll_initiative(p, surprised=surprised)
        p["initiative"] = init_total
        p["surprised"] = surprised
        initiative_list.append((uid, init_total, roll_text, surprised))

    initiative_list.sort(key=lambda x: x[1], reverse=True)
    order = [uid for uid, _, _, _ in initiative_list]

    # combat.participants 仅存 NPC/怪物
    combat_dict = {
        "round": 1,
        "participants": participants,
        "initiative_order": order,
        "current_actor_id": order[0],
    }

    order_desc = "\n".join(
        f"  {i+1}. {all_units[uid].get('name', uid)} [ID: {uid}] "
        f"(先攻 {init}{'，突袭劣势' if surprised else ''}；{roll_text})"
        for i, (uid, init, roll_text, surprised) in enumerate(initiative_list)
    )

    update: dict = {
        "combat": combat_dict,
        "phase": "combat",
        "active_combat_message_start": _active_combat_start_index(state),
        "messages": [
            ToolMessage(
                content=(
                    "战斗开始！第 1 回合。\n"
                    "先攻已经由工具完成结算；突袭单位的先攻劣势已计入下列骰式。"
                    "必须以此先攻顺序和当前行动者为准，不要自行重排或补骰。\n"
                    f"先攻顺序：\n{order_desc}\n\n"
                    f"当前行动者：{all_units[order[0]].get('name', order[0])} [ID: {order[0]}]"
                ),
                tool_call_id=tool_call_id,
            )
        ],
    }
    if player_dict:
        update["player"] = player_dict

    return Command(update=update)


# 场景单位公共实现：聚合入口和旧工具共用，避免两套生成/清理逻辑漂移。
def _spawn_ally_impl(
    profile_id: str,
    name: str | None,
    unit_id: str | None,
    state: dict | None,
    tool_call_id: str | None,
) -> Command:
    try:
        ally = get_ally_profile(profile_id)
    except ValueError as exc:
        return Command(update={"messages": [ToolMessage(content=str(exc), tool_call_id=tool_call_id)]})

    if name:
        ally["name"] = name
    ally["id"] = unit_id or ally.get("id") or profile_id
    ally["side"] = "ally"
    prepare_character_for_combat(ally, side="ally", fallback_id=ally["id"])

    scene_units: dict = state.get("scene_units") or {} if state else {}
    scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}
    scene_raw[ally["id"]] = ally

    return Command(update={
        "scene_units": scene_raw,
        "messages": [
            ToolMessage(content=f"友方 {ally['name']} [ID: {ally['id']}] 已加入场景。", tool_call_id=tool_call_id)
        ],
    })


def _spawn_monsters_impl(
    monster_index: str,
    count: int,
    faction: str,
    state: dict | None,
    tool_call_id: str | None,
) -> Command:
    try:
        new_combatants = spawn_combatants(monster_index, count, faction)
    except Exception as exc:
        return Command(update={"messages": [ToolMessage(content=f"生成战斗单位失败: {exc}", tool_call_id=tool_call_id)]})

    scene_units: dict = state.get("scene_units") or {} if state else {}
    scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}

    for combatant in new_combatants:
        scene_raw[combatant.id] = combatant.model_dump()

    names = [f"{combatant.name} [ID: {combatant.id}]" for combatant in new_combatants]
    return Command(update={
        "scene_units": scene_raw,
        "messages": [
            ToolMessage(
                content=(
                    f"成功在场景中生成了 {count} 只 {monster_index}: {', '.join(names)}。\n"
                    "已生成单位；不要再次生成同类单位，下一步应使用 manage_space 放置单位，或使用 start_combat 开战。"
                ),
                tool_call_id=tool_call_id,
            )
        ],
    })


def _clear_dead_units_impl(unit_ids: list[str] | None, state: dict | None, tool_call_id: str | None) -> Command:
    dead_units: dict = state.get("dead_units") or {} if state else {}
    dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}

    if not dead_raw:
        return Command(update={"messages": [ToolMessage(content="当前没有死亡单位。", tool_call_id=tool_call_id)]})

    if unit_ids:
        removed = [uid for uid in unit_ids if uid in dead_raw]
        for uid in removed:
            del dead_raw[uid]
        msg = f"已清除死亡单位: {', '.join(removed)}" if removed else "指定的 ID 不在死亡单位列表中。"
    else:
        count = len(dead_raw)
        dead_raw.clear()
        msg = f"已清除全部 {count} 个死亡单位。"

    return Command(update={"dead_units": dead_raw, "messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)]})


@tool
def manage_scene_units(
    action: Literal["help", "spawn_ally", "spawn_monsters", "clear_dead_units"],
    profile_id: str | None = None,
    name: str | None = None,
    unit_id: str | None = None,
    monster_index: str | None = None,
    count: int = 1,
    faction: str = "enemy",
    unit_ids: list[str] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """统一管理场景单位池中的友方、怪物与死亡单位档案。
    不确定模板、怪物 slug 或清理时机时先传 action="help" 读取场景单位技能说明。
    参数示例：{"action": "spawn_ally", "profile_id": "fighter_companion"}；{"action": "spawn_monsters", "monster_index": "goblin", "count": 4}。

    Args:
        action: help 读取说明，spawn_ally 创建友方，spawn_monsters 创建怪物，clear_dead_units 清理死亡档案。
        profile_id: 友方模板 ID，例如 fighter_companion、apprentice_wizard。
        name: 可选友方显示名。
        unit_id: 可选友方单位 ID。
        monster_index: 怪物 Open5e slug，例如 goblin、wolf、bugbear。
        count: 创建怪物数量。
        faction: 怪物阵营，通常为 enemy、ally 或 neutral。
        unit_ids: clear_dead_units 指定清理的死亡单位 ID；不传则清理全部。
    """
    if action == "help":
        return Command(update={"messages": [
            ToolMessage(content=load_skill_content("scene_unit_management"), tool_call_id=tool_call_id)
        ]})
    if action == "spawn_ally":
        return _spawn_ally_impl(profile_id or "", name, unit_id, state, tool_call_id)
    if action == "spawn_monsters":
        return _spawn_monsters_impl(monster_index or "", count, faction, state, tool_call_id)
    return _clear_dead_units_impl(unit_ids, state, tool_call_id)


@tool
def prepare_combat_start(
    combatant_ids: list[str],
    surprised_ids: list[str] | str | None = None,
    map_plan: dict | None = None,
    placements: list[dict] | None = None,
    reason: str = "",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """提交开战计划，由工作流统一执行 start_combat。
    这是探索叙事进入敌对轮次的边界工具：当伏击暴露、敌人开始攻击、玩家攻击或双方需要先攻/位置时，先提交开战计划，再叙述命中、伤害和回合推进。
    你仍负责根据剧情决定入场怪物/友方数量、地图规模、单位落点和突袭对象；不要直接口头宣布先攻结果。
    工作流会按 map_plan 建立或切换地图，按 placements 摆放单位，然后校验并投先攻。
    新版突袭只把被突袭者写入 surprised_ids，使其先攻检定劣势。
    map_plan 示例：{"action": "create", "name": "三猪小径伏击", "width": 150, "height": 120, "grid_size": 5}
    placements 示例：[{"unit_id": "player", "x": 20, "y": 60}, {"unit_id": "goblin_1", "x": 70, "y": 45}]

    Args:
        combatant_ids: 参加本次战斗的非玩家单位 ID 列表；敌人和友方都要列入。
        surprised_ids: 被突袭单位 ID；可用 "player" 指代当前玩家。
        map_plan: 地图方案。action="create" 创建并激活新地图；action="switch" 切到已有 map_id；不传则使用当前地图。
        placements: 本次开战前的单位落点，由你根据剧情决定坐标；unit_id 可用 "player" 指代当前玩家。
        reason: 简短说明为什么这些单位入场、谁被突袭。
    """
    normalized_surprised = _normalize_string_list_arg("surprised_ids", surprised_ids, tool_call_id)
    if isinstance(normalized_surprised, Command):
        return normalized_surprised
    return Command(update={
        "pending_combat_start": {
            "combatant_ids": [str(unit_id) for unit_id in combatant_ids],
            "surprised_ids": normalized_surprised,
            "map_plan": map_plan,
            "placements": list(placements or []),
            "reason": reason,
        },
        "messages": [ToolMessage(
            content=(
                "已提交开战计划，工作流将统一建图/切图、摆位、校验参战单位和突袭对象后开战。"
                f" 参战单位: {', '.join(combatant_ids)}。"
                f" 被突袭: {', '.join(normalized_surprised) if normalized_surprised else '无'}。"
                f" 地图: {map_plan or '沿用当前地图'}。"
                f" 落点数量: {len(placements or [])}。"
                f" 裁定理由: {reason or '未填写'}。"
            ),
            tool_call_id=tool_call_id,
            additional_kwargs={"hidden_from_ui": True},
        )],
    })


@tool
def delegate_combat_turn(
    actor_id: str,
    instruction: str,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """把当前行动者的本回合交给战斗执行器。
    主 Agent 只需把玩家最新输入与自己的战术裁定写进 instruction；执行器会用精简战斗上下文
    调用移动、攻击、施法、职业动作、物品和怪物动作等窄工具，并返回完整工具轨迹、
    前端战斗事件和是否建议结束回合。玩家、友方和怪物回合都优先通过这个入口落子，
    以保持主 Agent 在战斗阶段看到的工具集合稳定。

    Args:
        actor_id: 当前行动者 ID，必须等于 combat.current_actor_id。
        instruction: 本回合战术意图；玩家回合应包含玩家原话和你的裁定，例如
            “玩家说：我攻击最近的地精。裁定：player_hero 用长剑攻击 goblin_1；若距离不足先靠近。”。
    """
    return Command(update={
        "pending_combat_executor": {
            "actor_id": actor_id,
            "instruction": instruction,
        },
        "messages": [ToolMessage(
            content=f"已委托战斗执行器处理 {actor_id} 的回合。指令: {instruction}",
            tool_call_id=tool_call_id,
            additional_kwargs={"hidden_from_ui": True},
        )],
    })


@tool
def prepare_combat_end(
    outcomes: list[dict],
    reason: str = "",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """提交战斗结束计划，由工作流统一结算离场、俘虏、死亡档案和战斗 XP。
    每个仍在战斗中的非玩家单位都应有 outcome；不要直接调用 end_combat 跳过分类。
    result 可用：killed/dead、captured/surrendered/subdued/defeated、fled/escaped/retreated/departed、ally_safe。
    被俘、投降、被驱散、非致命击败但 HP 仍大于 0 的敌人必须标为 captured/surrendered/subdued/defeated，工作流会自动给 XP。

    Args:
        outcomes: 单位结局列表，例如 [{"unit_id": "goblin_1", "result": "captured"}]。
        reason: 简短说明战斗如何收束。
    """
    return Command(update={
        "pending_combat_end": {
            "outcomes": list(outcomes or []),
            "reason": reason,
        },
        "messages": [ToolMessage(
            content=f"已提交战斗结束计划，工作流将校验单位结局并统一结算 XP。结局数量: {len(outcomes or [])}。",
            tool_call_id=tool_call_id,
            additional_kwargs={"hidden_from_ui": True},
        )],
    })


@tool
def spawn_ally(
    profile_id: str,
    name: str | None = None,
    unit_id: str | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """根据友方角色模板生成受 Agent 控制的友方单位，并加入场景单位池。
    友方使用角色型规则：武器、法术位、已知法术、职业特性与反应资源都写在单位状态里。
    参数示例：{"profile_id": "apprentice_wizard", "name": "伊莲"}。

    Args:
        profile_id: 友方模板 ID，例如 "sildar"、"apprentice_wizard"、"acolyte_healer"。
        name: 可选显示名；不传则使用模板默认名。
        unit_id: 可选单位 ID；不传则按模板 ID 自动生成稳定 ID。
    """
    return _spawn_ally_impl(profile_id, name, unit_id, state, tool_call_id)


@tool
def spawn_monsters(
    monster_index: str,
    count: int = 1,
    faction: str = "enemy",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None
) -> Command:
    """根据怪物图鉴生成战斗单位实例并加入当前场景。
    怪物数据来自 Open5e SRD（使用英文 slug，如 "goblin", "owlbear", "adult-red-dragon"）。
    生成后的单位进入场景单位池（scene_units），需要通过 start_combat 指定参战。
    参数示例：{"monster_index": "goblin", "count": 4, "faction": "enemy"}。

    Args:
        monster_index: 怪物的 Open5e slug，必须是英文代号，例如 "goblin"、"wolf"、"bugbear"。
        count: 生成该单位的数量，例如 4。
        faction: 阵营，通常为 "enemy"、"ally" 或 "neutral"。
    """
    return _spawn_monsters_impl(monster_index, count, faction, state, tool_call_id)


@tool
def start_combat(
    combatant_ids: list[str],
    surprised_ids: list[str] | str | None = None,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """开始战斗：从场景单位池中选取指定 ID 的单位作为参战者，投先攻骰并排定行动顺序。
    前置条件：必须先用 spawn_monsters 生成单位，并用 manage_space 把玩家和参战单位放到当前地图。
    玩家角色会自动加入，无需在 combatant_ids 中指定；友方不是玩家，必须和敌人一样显式列入 combatant_ids。
    新版突袭只让被突袭者先攻劣势，不跳过首回合，也不禁用反应。
    开战前若需要判断突袭/被突袭，先对玩家小队或敌方小队整体做一次感知/潜行对抗，不要为每个单位逐个掷骰；判定完成后把被突袭单位 ID 放入 surprised_ids。
    参数示例：{"combatant_ids": ["fighter_companion", "goblin_1", "goblin_2"], "surprised_ids": ["player"]}。

    Args:
        combatant_ids: 从场景单位池中参加本次战斗的非玩家单位 ID 列表；敌人和友方都要放进来，不要把玩家 ID 放进来。
        surprised_ids: 被突袭的单位 ID；这些单位先攻检定用劣势。可用 "player" 指代当前玩家。
    """
    return _execute_start_combat(
        combatant_ids,
        surprised_ids,
        state=state,
        tool_call_id=tool_call_id,
    )


@tool
def attack_action(
    attacker_id: str,
    target_id: str,
    attack_name: str | None = None,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
    maneuver_id: str | None = None,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """执行一次攻击动作：命中判定 → 暴击检测 → 伤害结算 → 扣血。
    状态效果（如目盲、隐形等）的优劣势会自动叠加计算。
    玩家攻击结束后如果没有其他额外动作，可以询问玩家或代表玩家调用 `next_turn`。
    参数示例：{"attacker_id": "温良", "target_id": "goblin_1", "attack_name": "Longsword", "advantage": "normal"}。

    Args:
        attacker_id: 攻击者 ID，必须是当前回合行动者。
        target_id: 目标单位 ID。
        attack_name: 使用的攻击名称或动作名；不确定时可省略，让工具使用第一个攻击方式。
        advantage: 攻击优劣势，只能是 "normal"、"advantage" 或 "disadvantage"。
        maneuver_id: 可选命中后战技，目前支持 "trip_attack"、"menacing_attack"、"pushing_attack"；只有攻击命中后才会消耗卓越骰并结算效果。
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    # 获取玩家字典（如有）
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    # 通过统一接口获取攻防双方
    attacker = get_combatant(combat_dict, player_dict, attacker_id)
    target = get_combatant(combat_dict, player_dict, target_id)
    resolved_attacker_id = canonical_combatant_id(attacker, attacker_id)
    resolved_target_id = canonical_combatant_id(target, target_id)

    def _reject(msg: str) -> Command:
        return Command(update={"messages": [
            ToolMessage(content=f"[攻击失败] {msg}", tool_call_id=tool_call_id)
        ]})

    if not attacker:
        return _reject(f"找不到攻击者 '{attacker_id}'。")
    if not target:
        return _reject(f"找不到目标 '{target_id}'。")
    if combat_dict.get("current_actor_id") != resolved_attacker_id:
        return _reject(f"现在不是 {attacker.get('name', attacker_id)} 的回合，当前行动者为 {combat_dict.get('current_actor_id')}。")
    if attacker.get("hp", 0) <= 0:
        return _reject(f"{attacker.get('name', attacker_id)} 已经倒下，无法攻击；若这是玩家回合，应先进行死亡豁免。")
    if target.get("hp", 0) <= 0 and not tracks_death_saves(target):
        return _reject(f"目标 {target.get('name', target_id)} 已经倒下，无法攻击。")
    if not has_action_resource(attacker, "action"):
        return _reject(f"{attacker.get('name', attacker_id)} 本回合的动作已用尽。")

    chosen_attack = choose_attack(attacker, attack_name)
    if attack_name and chosen_attack is None:
        available = ", ".join(available_attack_names(attacker)) or "无"
        return _reject(f"未知攻击 '{attack_name}'。可用攻击: {available}。")
    canonical_space = canonicalize_player_space(state.get("space"), player_dict)
    if distance_error := validate_attack_distance(canonical_space, resolved_attacker_id, resolved_target_id, chosen_attack):
        return _reject(distance_error)

    roll_info = roll_attack_hit(attacker, target, attack_name, advantage, state)
    if maneuver_id:
        roll_info["maneuver_id"] = maneuver_id
    auto_reaction_lines = apply_automatic_hit_reaction(target, attacker, roll_info, state)

    if (
        player_dict
        and attacker.get("side") != "player"
        and target is player_dict
        and roll_info.get("hit")
        and not roll_info.get("blocked")
    ):
        reaction_context = {
            "attacker": attacker.get("name", attacker_id),
            "attack_roll": {
                "raw_roll": roll_info.get("raw_roll", roll_info.get("natural", 0)),
                "attack_bonus": roll_info.get("attack_bonus", 0),
                "final_total": roll_info.get("hit_total", 0),
                "hit_total": roll_info.get("hit_total", 0),
                "target_ac": roll_info.get("target_ac", 10),
            },
        }
        available_reactions = get_available_reactions(player_dict, "on_hit", reaction_context)
        if available_reactions:
            pending_reaction_msg = ToolMessage(
                content=(
                    f"{attacker.get('name', attacker_id)} 的攻击命中了 {target.get('name', target_id)}，"
                    "已进入反应判定，等待玩家选择。"
                ),
                tool_call_id=tool_call_id,
                additional_kwargs={"hidden_from_ui": True},
            )
            return Command(
                update={
                    "combat": combat_dict,
                    "player": player_dict,
                    "messages": [pending_reaction_msg],
                    "pending_reaction": build_pending_reaction_state(attacker, target, roll_info, available_reactions),
                    "reaction_choice": None,
                }
            )

    lines, _, hp_change, _ = apply_attack_damage(attacker, target, roll_info)
    lines = auto_reaction_lines + lines

    attack_roll_payload = build_attack_roll_event_payload(roll_info)

    tool_message_kwargs = {}
    if attack_roll_payload:
        tool_message_kwargs["additional_kwargs"] = {"attack_roll": attack_roll_payload}

    tool_msg = ToolMessage(
        content="\n".join(lines),
        tool_call_id=tool_call_id,
        **tool_message_kwargs,
    )

    # 玩家数据已在 player_dict 上原地修改，无需手动同步
    update: dict = {
        "combat": combat_dict,
        "messages": [tool_msg],
    }
    if player_dict:
        update["player"] = player_dict
    if hp_change:
        update["hp_changes"] = [hp_change]

    return Command(update=update)


@tool
def next_turn(
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结束当前行动者回合，并推进到下一个存活单位。如果所有人都行动过，则进入新的回合。
    参数示例：{}。
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if not combat_dict.get("initiative_order"):
        return "先攻顺序为空，请先调用 start_combat。"

    result_text = advance_turn(combat_dict, player_dict, state)

    update: dict = {
        "combat": combat_dict,
        "messages": [
            ToolMessage(content=result_text, tool_call_id=tool_call_id)
        ],
    }
    if player_dict:
        update["player"] = player_dict

    return Command(update=update)


@tool
def end_combat(
    departed_unit_ids: list[str] | str | None = None,
    defeated_unit_ids: list[str] | str | None = None,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结束当前战斗。存活的非玩家单位回归场景，死亡单位归入死亡档案（可搜尸等）。
    敌人逃跑、撤退、传送离开或剧情退场时，把单位 ID 放入 departed_unit_ids；
    这些单位不会进入死亡档案，也不会继续留在当前地图或场景单位池。
    敌人投降、被俘、被驱散或以非 0 HP 方式被战胜时，把单位 ID 放入 defeated_unit_ids 领取战斗 XP。
    参数示例：{}；{"departed_unit_ids": ["goblin_1"]}；{"defeated_unit_ids": ["goblin_1"]}。

    Args:
        departed_unit_ids: 存活但已离开当前场景的非玩家单位 ID 列表。
        defeated_unit_ids: 非 0 HP 但已被玩家战胜的敌方单位 ID 列表。
    """
    departed_unit_ids = _normalize_string_list_arg("departed_unit_ids", departed_unit_ids, tool_call_id)
    if isinstance(departed_unit_ids, Command):
        return departed_unit_ids
    defeated_unit_ids = _normalize_string_list_arg("defeated_unit_ids", defeated_unit_ids, tool_call_id)
    if isinstance(defeated_unit_ids, Command):
        return defeated_unit_ids
    departed_ids = {str(unit_id).strip() for unit_id in departed_unit_ids if str(unit_id).strip()}
    defeated_ids = {str(unit_id).strip() for unit_id in defeated_unit_ids if str(unit_id).strip()}

    combat_raw = state.get("combat")
    summary = "战斗结束。"
    update: dict = {
        "combat": None,
        "phase": "exploration",
        "active_combat_message_start": None,
    }

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        rounds = combat_dict.get("round", 0)
        participants = combat_dict.get("participants", {})

        alive_names: list[str] = []
        fallen_names: list[str] = []

        scene_units: dict = state.get("scene_units") or {}
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}
        dead_units: dict = state.get("dead_units") or {}
        dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}
        departed_units: dict = state.get("departed_units") or {}
        departed_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in departed_units.items()} if hasattr(departed_units, "items") else {}

        # 处理玩家 — HP 已在 player_dict 上保持最新，只需清除战斗覆盖字段
        if player_dict:
            if player_dict.get("hp", 0) > 0:
                alive_names.append(player_dict.get("name", "player"))
            else:
                fallen_names.append(player_dict.get("name", "player"))
            clear_player_combat_fields(player_dict)

        # 处理非玩家单位；友方不会因倒地被清理，便于后续治疗或复活。
        departed_names: list[str] = []
        for uid, p in participants.items():
            name = p.get("name", uid)
            if p.get("side") == "ally":
                alive_names.append(name)
                scene_raw[uid] = p
            elif uid in departed_ids:
                departed_names.append(name)
                departure_reason = "defeated" if uid in defeated_ids else "departed"
                departed_raw[uid] = {**p, "departure_reason": departure_reason}
                scene_raw.pop(uid, None)
            elif p.get("hp", 0) > 0:
                alive_names.append(name)
                scene_raw[uid] = p
            else:
                fallen_names.append(name)
                dead_raw[uid] = p
                scene_raw.pop(uid, None)

        parts = [f"共进行了 {rounds} 回合。"]
        if alive_names:
            parts.append(f"存活: {', '.join(alive_names)}")
        if fallen_names:
            parts.append(f"倒下: {', '.join(fallen_names)}")
        if departed_names:
            parts.append(f"离场: {', '.join(departed_names)}")
        summary = " ".join(parts)

        xp_award = grant_combat_xp(player_dict, participants, defeated_ids)
        if xp_award:
            awards_text = ", ".join(f"{award['name']} {award['xp']} XP" for award in xp_award["awards"])
            summary += f" 战斗 XP: +{xp_award['total_xp']}（{awards_text}），当前 XP {xp_award['current_xp']}。"

        adventure_update, recorded_events = _adventure_after_combat_update(state)
        if adventure_update:
            update["adventure"] = adventure_update
            if recorded_events:
                summary += f" 已记录冒险事件: {', '.join(recorded_events)}。"
            summary += " 节点收束与后续书签推进由后台导航器处理。"

        removed_space_unit_ids = [*dead_raw.keys(), *departed_ids]
        if removed_space_unit_ids:
            space_raw = state.get("space")
            cleaned_space = _remove_space_units(space_raw, removed_space_unit_ids)
            if cleaned_space is not None:
                update["space"] = cleaned_space

        update["scene_units"] = scene_raw
        update["dead_units"] = dead_raw
        update["departed_units"] = departed_raw

    if player_dict:
        update["player"] = player_dict

    update["messages"] = [ToolMessage(content=summary, tool_call_id=tool_call_id)]
    return Command(update=update)


