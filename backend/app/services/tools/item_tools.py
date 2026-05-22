"""背包管理工具 — 当前只实现冒险常见药水。"""

from __future__ import annotations

from typing import Annotated, Literal

import d20
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.conditions._base import build_condition_extra, create_condition, upsert_condition
from app.equipment.items import CONSUMABLE_ITEMS, create_inventory_item, get_consumable_item
from app.services.tools._helpers import (
    consume_action_resource,
    get_combatant,
    has_action_resource,
    is_player_reference,
    reset_death_save_state,
    resolve_player_reference_id,
)
from app.space.geometry import validate_unit_distance


UseItemMode = Literal["drink", "feed", "throw"]
InventoryAction = Literal["list_shop", "buy", "add", "use"]
HEALING_POTION_ROLLS = {
    "potion_of_healing": "2d4+2",
    "potion_of_greater_healing": "4d4+4",
}


def _state_value_to_dict(value) -> dict | None:
    """工具写入前统一转成普通 dict。"""
    if not value:
        return None
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def _mapping_state_to_dict(value) -> dict:
    """场景单位和战斗单位映射都按可写 dict 处理。"""
    if not value:
        return {}
    if hasattr(value, "items"):
        return {key: _state_value_to_dict(item) or {} for key, item in value.items()}
    return {}


def _item_context(state: dict) -> dict:
    """一次工具调用共享同一份状态快照，避免 actor/target 回写互相覆盖。"""
    return {
        "player": _state_value_to_dict(state.get("player")),
        "combat": _state_value_to_dict(state.get("combat")),
        "scene_units": _mapping_state_to_dict(state.get("scene_units")),
        "update": {},
    }


def _locate_unit(ctx: dict, unit_id: str) -> tuple[dict | None, str, str]:
    """按玩家、战斗参与者、场景单位顺序定位单位。"""
    player = ctx["player"]
    combat = ctx["combat"]
    scene_units = ctx["scene_units"]

    if player and is_player_reference(player, unit_id):
        player_id = resolve_player_reference_id(player, unit_id)
        return player, player_id, "player"

    if combat:
        unit = get_combatant(combat, player, unit_id)
        if unit:
            ctx["update"]["combat"] = combat
            if player and unit is player:
                ctx["update"]["player"] = player
                return unit, player["id"], "player"
            return unit, unit_id, "combat"

    if unit_id in scene_units:
        ctx["update"]["scene_units"] = scene_units
        return scene_units[unit_id], unit_id, "scene"

    return None, unit_id, ""


def _write_unit(ctx: dict, unit: dict, unit_id: str, source: str) -> dict:
    """把被修改单位写回原容器。"""
    update = ctx["update"]
    if source == "player":
        update["player"] = unit
    elif source == "combat":
        ctx["combat"]["participants"][unit_id] = unit
        update["combat"] = ctx["combat"]
    elif source == "scene":
        ctx["scene_units"][unit_id] = unit
        update["scene_units"] = ctx["scene_units"]
    return update


def _find_inventory_item(unit: dict, item_id: str) -> dict | None:
    """背包条目按稳定 id 匹配，数量必须大于 0。"""
    for item in unit.get("inventory", []) or []:
        if item.get("id") == item_id and int(item.get("quantity", 0) or 0) > 0:
            return item
    return None


def _consume_inventory_item(unit: dict, item: dict) -> None:
    """药水消耗后数量减一；保留 0 数量以避免前端突然丢失历史名称。"""
    item["quantity"] = int(item.get("quantity", 0) or 0) - 1
    unit["inventory"] = [entry for entry in unit.get("inventory", []) if int(entry.get("quantity", 0) or 0) > 0]


def _add_inventory_item(unit: dict, item_id: str, quantity: int) -> dict:
    """购物只叠加同 ID 条目，避免背包出现多份同名药水。"""
    inventory = list(unit.get("inventory", []) or [])
    for entry in inventory:
        if entry.get("id") == item_id:
            entry["quantity"] = int(entry.get("quantity", 0) or 0) + quantity
            unit["inventory"] = inventory
            return entry

    item = create_inventory_item(item_id, quantity)
    inventory.append(item)
    unit["inventory"] = inventory
    return item


def _shop_catalog_text() -> str:
    """购物工具每次返回当前价目表，确保模型后续不会靠记忆猜价格。"""
    lines = ["[商店待售清单]"]
    for item in CONSUMABLE_ITEMS.values():
        lines.append(f"- {item.id}: {item.name} / {item.name_en}，{item.price_gp} gp。{item.description}")
    return "\n".join(lines)


def _inventory_message(content: str, tool_call_id: str | None) -> ToolMessage:
    """背包工具消息带稳定 name，便于上下文压缩器按工具类型保留关键信息。"""
    return ToolMessage(content=content, tool_call_id=tool_call_id, name="manage_inventory")


def _combat_action_type(mode: UseItemMode, actor_id: str, target_id: str) -> str:
    """2024 规则：自己喝用附赠动作，给别人用消耗动作。"""
    if mode == "drink" and actor_id == target_id:
        return "bonus_action"
    return "action"


def _validate_item_mode(mode: UseItemMode, actor_id: str, target_id: str) -> str | None:
    """药水模式必须表达真实动作，避免“喝药”被误用成远程治疗他人。"""
    if mode == "drink" and actor_id != target_id:
        return "drink 只能由使用者自己饮用；给别人用药请显式使用 feed（5 尺）或 throw（20 尺）。"
    if mode in {"feed", "throw"} and actor_id == target_id:
        return "给自己使用药水请使用 drink，不要用 feed 或 throw。"
    return None


def _validate_item_range(mode: UseItemMode, state: dict, actor_id: str, target_id: str) -> str | None:
    """喂药必须贴身，投掷药水保留轻量 20 尺限制。"""
    if actor_id == target_id:
        return None
    if mode == "feed":
        return validate_unit_distance(state.get("space"), actor_id, target_id, 5, action_label="喂药")
    if mode == "throw":
        return validate_unit_distance(state.get("space"), actor_id, target_id, 20, action_label="投掷药水")
    return None


def _apply_potion(item_id: str, target: dict, lines: list[str], hp_changes: list[dict]) -> None:
    """三种基础药水的实际效果。"""
    if item_id in HEALING_POTION_ROLLS:
        old_hp = int(target.get("hp", 0) or 0)
        max_hp = int(target.get("max_hp", old_hp) or old_hp)
        roll = d20.roll(HEALING_POTION_ROLLS[item_id])
        target["hp"] = min(max_hp, old_hp + roll.total)
        if target["hp"] > 0:
            reset_death_save_state(target)
        hp_changes.append({"id": target.get("id", target.get("name", "target")), "name": target.get("name", "target"), "old_hp": old_hp, "new_hp": target["hp"], "max_hp": max_hp})
        lines.append(f"{get_consumable_item(item_id).name}恢复 {target['hp'] - old_hp} HP（{roll}）。HP {old_hp} -> {target['hp']} / {max_hp}。")
        return

    if item_id == "potion_of_invisibility":
        upsert_condition(
            target,
            create_condition(
                "invisible",
                source_id="potion_of_invisibility",
                duration=600,
                extra=build_condition_extra(break_on=["attack", "spell"], source_item=item_id),
            ),
            replace_existing=True,
        )
        lines.append("隐身药水生效：目标隐形 1 小时；攻击或施法会提前显形。")
        return

    if item_id == "potion_of_vitality":
        old_conditions = [condition.get("id", "") for condition in target.get("conditions", [])]
        target["conditions"] = [
            condition for condition in target.get("conditions", [])
            if condition.get("id") not in {"exhausted", "poisoned", "diseased", "disease"}
        ]
        target["hit_dice_healing_maximized_until"] = "24h"
        removed = [condition_id for condition_id in old_conditions if condition_id not in [condition.get("id") for condition in target["conditions"]]]
        lines.append("活力药水生效：清除力竭、疾病和毒素；24小时内生命骰治疗取最大值。")
        if removed:
            lines.append(f"清除状态: {', '.join(removed)}。")


def _use_item_intro(mode: UseItemMode, actor: dict, actor_id: str, target: dict, target_id: str, item_name: str) -> str:
    """战报首行直接说明喝、喂、投掷，避免玩家误读动作和距离。"""
    actor_name = actor.get("name", actor_id)
    target_name = target.get("name", target_id)
    if mode == "drink":
        return f"{actor_name} 饮下 {item_name}。"
    if mode == "feed":
        return f"{actor_name} 贴身给 {target_name} 喂下 {item_name}。"
    return f"{actor_name} 将 {item_name} 投掷给 {target_name}。"


def _use_item_command(
    item_id: str,
    actor_id: str = "player",
    target_id: str = "",
    mode: UseItemMode = "drink",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    if item_id not in CONSUMABLE_ITEMS:
        return Command(update={"messages": [ToolMessage(content=f"暂不支持的道具: {item_id}。", tool_call_id=tool_call_id)]})

    ctx = _item_context(state)
    actor, resolved_actor_id, actor_source = _locate_unit(ctx, actor_id)
    if not actor:
        return Command(update={"messages": [ToolMessage(content=f"找不到道具使用者 '{actor_id}'。", tool_call_id=tool_call_id)]})

    effective_target_id = str(target_id or "").strip() or resolved_actor_id
    target, resolved_target_id, target_source = _locate_unit(ctx, effective_target_id)
    if not target:
        return Command(update={"messages": [ToolMessage(content=f"找不到道具目标 '{effective_target_id}'。", tool_call_id=tool_call_id)]})

    if mode_error := _validate_item_mode(mode, resolved_actor_id, resolved_target_id):
        return Command(update={"messages": [ToolMessage(content=mode_error, tool_call_id=tool_call_id)]})

    item = _find_inventory_item(actor, item_id)
    if not item:
        return Command(update={"messages": [ToolMessage(content=f"{actor.get('name', resolved_actor_id)} 没有可用的 {item_id}。", tool_call_id=tool_call_id)]})

    if range_error := _validate_item_range(mode, state, resolved_actor_id, resolved_target_id):
        return Command(update={"messages": [ToolMessage(content=range_error, tool_call_id=tool_call_id)]})

    combat = ctx.get("combat")
    action_type = _combat_action_type(mode, resolved_actor_id, resolved_target_id)
    if combat:
        if combat.get("current_actor_id") != resolved_actor_id:
            return Command(update={"messages": [ToolMessage(content=f"现在不是 {actor.get('name', resolved_actor_id)} 的回合。", tool_call_id=tool_call_id)]})
        if not has_action_resource(actor, action_type):
            return Command(update={"messages": [ToolMessage(content=f"{actor.get('name', resolved_actor_id)} 本回合没有可用的{'附赠动作' if action_type == 'bonus_action' else '动作'}。", tool_call_id=tool_call_id)]})
        consume_action_resource(actor, action_type)

    item_def = get_consumable_item(item_id)
    lines = [_use_item_intro(mode, actor, resolved_actor_id, target, resolved_target_id, item_def.name)]
    hp_changes: list[dict] = []
    _apply_potion(item_id, target, lines, hp_changes)
    _consume_inventory_item(actor, item)

    update = {}
    update.update(_write_unit(ctx, actor, resolved_actor_id, actor_source))
    update.update(_write_unit(ctx, target, resolved_target_id, target_source))
    if hp_changes:
        update["hp_changes"] = hp_changes
    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)


def _buy_item_command(
    item_id: str = "",
    quantity: int = 1,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    if not item_id.strip():
        return Command(update={"messages": [_inventory_message(_shop_catalog_text(), tool_call_id)]})

    if item_id not in CONSUMABLE_ITEMS:
        return Command(update={"messages": [_inventory_message(f"暂不支持购买的道具: {item_id}。", tool_call_id)]})
    if quantity <= 0:
        return Command(update={"messages": [_inventory_message("购买数量必须大于 0。", tool_call_id)]})

    player = _state_value_to_dict(state.get("player") if state else None)
    if not player:
        return Command(update={"messages": [_inventory_message("玩家尚未加载，无法购物。", tool_call_id)]})

    item = get_consumable_item(item_id)
    total_price = item.price_gp * quantity
    coins = {key: int(value) for key, value in dict(player.get("coins", {})).items()}
    current_gp = coins.get("gp", 0)
    if current_gp < total_price:
        return Command(update={"messages": [_inventory_message(f"GP 不足：购买 {quantity} 个{item.name}需要 {total_price} gp，当前只有 {current_gp} gp。", tool_call_id)]})

    coins["gp"] = current_gp - total_price
    player["coins"] = coins
    inventory_item = _add_inventory_item(player, item_id, quantity)
    content = (
        f"购物完成：购买 {quantity} 个{item.name}，花费 {total_price} gp；"
        f"剩余 {coins['gp']} gp，背包现有 {inventory_item['quantity']} 个。"
    )
    return Command(update={"player": player, "messages": [_inventory_message(content, tool_call_id)]})


def _add_item_command(
    item_id: str,
    quantity: int,
    actor_id: str,
    reason: str,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """剧情拾取只加入已定义消耗品；模组节点奖励仍由 claim_adventure_reward 发放。"""
    if item_id not in CONSUMABLE_ITEMS:
        return Command(update={"messages": [_inventory_message(f"暂不支持加入背包的道具: {item_id}。", tool_call_id)]})
    if quantity <= 0:
        return Command(update={"messages": [_inventory_message("加入数量必须大于 0。", tool_call_id)]})
    if not reason.strip():
        return Command(update={"messages": [_inventory_message("剧情拾取或赠予必须提供 reason；模组节点奖励请使用 claim_adventure_reward。", tool_call_id)]})

    ctx = _item_context(state or {})
    owner, resolved_owner_id, owner_source = _locate_unit(ctx, actor_id)
    if not owner:
        return Command(update={"messages": [_inventory_message(f"找不到背包持有者 '{actor_id}'。", tool_call_id)]})

    item_def = get_consumable_item(item_id)
    entry = _add_inventory_item(owner, item_id, quantity)
    update = _write_unit(ctx, owner, resolved_owner_id, owner_source)
    update["messages"] = [
        _inventory_message(
            f"已加入背包：{owner.get('name', resolved_owner_id)} 获得 {quantity} 个{item_def.name}。"
            f" 来源：{reason}。当前数量 {entry['quantity']} 个。",
            tool_call_id,
        )
    ]
    return Command(update=update)


@tool
def manage_inventory(
    action: InventoryAction,
    item_id: str = "",
    quantity: int = 1,
    actor_id: str = "player",
    target_id: str = "",
    mode: UseItemMode = "drink",
    reason: str = "",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """统一管理背包里的基础消耗品。

    list_shop 查看药水价目表；buy 在可信交易机会中扣 GP 购买；add 仅用于临时剧情拾取、
    NPC 赠予或普通补给发放，不处理模组节点奖励、金币或 XP；use 使用背包药水并结算动作经济。

    Args:
        action: list_shop 查看价目表，buy 购买，add 剧情拾取/赠予/补给，use 使用道具。
        item_id: 道具 ID，当前支持 potion_of_healing、potion_of_greater_healing、potion_of_invisibility、potion_of_vitality。
        quantity: buy/add 的数量。
        actor_id: use 时为使用者；add 时为背包持有者；默认 player。
        target_id: use 时的目标；省略则为 actor_id。
        mode: use 的方式，drink 自饮，feed 贴身喂药，throw 投掷药水。
        reason: add 的剧情来源说明，必须是临时拾取、赠予或补给，不得用于节点奖励。
    """
    if action == "list_shop":
        return Command(update={"messages": [_inventory_message(_shop_catalog_text(), tool_call_id)]})
    if action == "buy":
        return _buy_item_command(item_id=item_id, quantity=quantity, state=state, tool_call_id=tool_call_id)
    if action == "add":
        return _add_item_command(
            item_id=item_id,
            quantity=quantity,
            actor_id=actor_id,
            reason=reason,
            state=state,
            tool_call_id=tool_call_id,
        )
    return _use_item_command(
        item_id=item_id,
        actor_id=actor_id,
        target_id=target_id,
        mode=mode,
        state=state,
        tool_call_id=tool_call_id,
    )
