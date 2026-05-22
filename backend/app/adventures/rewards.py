"""冒险奖励状态投影 — runtime 判定待领取，工具负责实际写回。"""

from __future__ import annotations

from typing import Any


SUPPORTED_REWARD_TYPES = frozenset({"xp", "gold", "treasure", "item"})
REWARD_ARRIVAL_EVENT_PREFIXES = ("enter_", "arrive_", "arrived_", "reach_")
COIN_CURRENCIES = frozenset({"cp", "sp", "ep", "gp", "pp"})


def normalize_pending_reward_grants(values: list[Any] | None) -> list[dict[str, Any]]:
    """清洗待领取奖励队列，保证同一个 reward_id 只出现一次。"""
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values or []:
        if not isinstance(item, dict):
            continue
        reward_id = str(item.get("id", "")).strip()
        if not reward_id or reward_id in seen:
            continue
        seen.add(reward_id)
        pending.append(
            {
                "id": reward_id,
                "node_id": str(item.get("node_id", "")).strip(),
                "type": str(item.get("type", "")).strip().lower(),
                "amount": item.get("amount", 0),
                "scope": str(item.get("scope", "")).strip(),
                "currency": str(item.get("currency", "")).strip().lower(),
                "description": str(item.get("description", "")).strip(),
                "requires": [str(requirement) for requirement in item.get("requires", [])],
            }
        )
    return pending


def reward_requires_met(reward: dict[str, Any], adventure: dict[str, Any]) -> bool:
    """奖励条件只认已知线索与已完成事件，避免主模型凭空领取。"""
    required = reward.get("requires", [])
    if not required:
        return True
    known_clues = set(adventure.get("known_clue_ids", []))
    completed_events = set(adventure.get("completed_event_ids", []))
    return all(item in known_clues or item in completed_events for item in required)


def reward_id_for(node_id: str, reward: dict[str, Any], index: int) -> str:
    """奖励 id 是防重发锁；缺失时退回节点内序号。"""
    return str(reward.get("id") or f"{node_id}:{index}")


def pending_reward_from_node(node_id: str, reward: dict[str, Any], index: int) -> dict[str, Any]:
    """把节点奖励投影成主模型可领取的短记录。"""
    return {
        "id": reward_id_for(node_id, reward, index),
        "node_id": node_id,
        "type": str(reward.get("type", "")).strip().lower(),
        "amount": reward.get("amount", 0),
        "scope": str(reward.get("scope", "")).strip(),
        "currency": str(reward.get("currency", "")).strip().lower(),
        "description": str(reward.get("description", "")).strip(),
        "requires": [str(requirement) for requirement in reward.get("requires", [])],
    }


def sync_pending_node_rewards(adventure: dict[str, Any], node: Any) -> bool:
    """把当前节点已满足但未领取的奖励同步到 pending_reward_grants。"""
    arrival_changed = _reconcile_reward_arrival_requirements(adventure, node)
    claimed = set(adventure.get("claimed_reward_ids", []))
    previous = normalize_pending_reward_grants(adventure.get("pending_reward_grants", []))
    pending = [item for item in previous if item["id"] not in claimed]
    existing_ids = {item["id"] for item in pending}

    for index, reward in enumerate(getattr(node, "rewards", []) or []):
        if not isinstance(reward, dict):
            continue
        if str(reward.get("type", "")).strip().lower() not in SUPPORTED_REWARD_TYPES:
            continue
        reward_id = reward_id_for(node.id, reward, index)
        if reward_id in claimed or reward_id in existing_ids:
            continue
        if not reward_requires_met(reward, adventure):
            continue
        pending.append(pending_reward_from_node(node.id, reward, index))
        existing_ids.add(reward_id)

    changed = arrival_changed or pending != previous
    adventure["pending_reward_grants"] = pending
    return changed


def _reconcile_reward_arrival_requirements(adventure: dict[str, Any], node: Any) -> bool:
    """奖励同步前补齐当前节点声明过的抵达事件，避免奖励系统和转场系统分叉。"""
    rewards = [item for item in getattr(node, "rewards", []) or [] if isinstance(item, dict)]
    if not rewards:
        return False
    node_arrivals = {
        event_id
        for event_id in getattr(node, "events", []) or []
        if str(event_id).startswith(REWARD_ARRIVAL_EVENT_PREFIXES)
    }
    required_arrivals = {
        str(requirement)
        for reward in rewards
        for requirement in reward.get("requires", [])
        if str(requirement).startswith(REWARD_ARRIVAL_EVENT_PREFIXES)
    }
    completed = adventure["completed_event_ids"]
    changed = False
    for event_id in sorted(node_arrivals & required_arrivals):
        if event_id not in completed:
            completed.append(event_id)
            changed = True
    return changed


def find_pending_reward(adventure: dict[str, Any], reward_id: str) -> dict[str, Any] | None:
    """按 reward_id 查找待领取奖励。"""
    for reward in normalize_pending_reward_grants(adventure.get("pending_reward_grants", [])):
        if reward["id"] == reward_id:
            return reward
    return None


def remove_pending_reward(adventure: dict[str, Any], reward_id: str) -> bool:
    """领取成功后移除待领取记录。"""
    previous = normalize_pending_reward_grants(adventure.get("pending_reward_grants", []))
    pending = [item for item in previous if item["id"] != reward_id]
    adventure["pending_reward_grants"] = pending
    return pending != previous


def claim_pending_reward(
    state: dict[str, Any],
    reward_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """领取剧情奖励并同步 claimed/pending 两侧状态。"""
    from app.adventures.navigation import normalize_adventure_state

    adventure = normalize_adventure_state(state.get("adventure"))
    reward = find_pending_reward(adventure, reward_id)
    if reward is None:
        return adventure, None, {
            "ok": False,
            "error": f"待领取奖励不存在: {reward_id}",
            "pending_reward_ids": [item["id"] for item in normalize_pending_reward_grants(adventure.get("pending_reward_grants", []))],
            "claimed_reward_ids": adventure.get("claimed_reward_ids", []),
        }

    if reward["id"] in adventure.get("claimed_reward_ids", []):
        remove_pending_reward(adventure, reward["id"])
        return adventure, None, {
            "ok": False,
            "error": f"奖励已领取: {reward['id']}",
            "reward": reward,
            "claimed_reward_ids": adventure.get("claimed_reward_ids", []),
        }

    reward_type = str(reward.get("type", "")).lower()
    if reward_type not in SUPPORTED_REWARD_TYPES:
        return adventure, None, {
            "ok": False,
            "error": f"暂不支持的奖励类型: {reward_type or 'unknown'}",
            "reward": reward,
            "claimed_reward_ids": adventure.get("claimed_reward_ids", []),
        }

    player, error = _apply_reward_to_player(state, reward)
    if error:
        return adventure, None, {
            "ok": False,
            "error": error,
            "reward": reward,
            "claimed_reward_ids": adventure.get("claimed_reward_ids", []),
        }
    claimed = adventure.setdefault("claimed_reward_ids", [])
    if reward["id"] not in claimed:
        claimed.append(reward["id"])
    remove_pending_reward(adventure, reward["id"])
    return adventure, player, {
        "ok": True,
        "reward": reward,
        "claimed_reward_ids": adventure.get("claimed_reward_ids", []),
        "pending_reward_ids": [item["id"] for item in normalize_pending_reward_grants(adventure.get("pending_reward_grants", []))],
    }


def _apply_reward_to_player(state: dict[str, Any], reward: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """把节点奖励落到角色卡；财宝保留原文，避免误拆混合币种和可售物。"""
    player_raw = state.get("player")
    if not player_raw:
        return None, "玩家尚未加载角色卡。"
    player = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
    reward_type = str(reward["type"]).lower()
    if reward_type == "xp":
        return _apply_xp_reward(player, reward)
    if reward_type == "gold":
        return _apply_coin_reward(player, reward)
    if reward_type in {"item", "treasure"}:
        return _apply_inventory_reward(player, reward)
    return None, f"暂不支持的奖励类型: {reward_type or 'unknown'}"


def _apply_xp_reward(player: dict[str, Any], reward: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """XP 直接累加到角色卡，并把前后值写回工具结果。"""
    amount = int(reward.get("amount", 0) or 0)
    if amount <= 0:
        return None, f"奖励金额无效: {reward['id']}"
    previous_xp = int(player.get("xp", 0) or 0)
    player["xp"] = previous_xp + amount
    reward["previous_xp"] = previous_xp
    reward["current_xp"] = player["xp"]
    return player, ""


def _apply_coin_reward(player: dict[str, Any], reward: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """金币类奖励进入钱袋；节点 scope 只用于说明，不在单人角色卡里拆分队伍份额。"""
    amount = int(reward.get("amount", 0) or 0)
    if amount <= 0:
        return None, f"奖励金额无效: {reward['id']}"
    currency = str(reward.get("currency") or "gp").lower()
    if currency not in COIN_CURRENCIES:
        return None, f"暂不支持的货币类型: {currency}"
    coins = {key: int(value) for key, value in dict(player.get("coins", {})).items()}
    previous_amount = coins.get(currency, 0)
    coins[currency] = previous_amount + amount
    player["coins"] = coins
    reward["previous_amount"] = previous_amount
    reward["current_amount"] = coins[currency]
    return player, ""


def _apply_inventory_reward(player: dict[str, Any], reward: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """道具和财宝作为奖励条目入包，防重发由 claimed_reward_ids 兜住。"""
    amount = int(reward.get("amount", 1) or 1)
    if amount <= 0:
        return None, f"奖励数量无效: {reward['id']}"
    inventory = list(player.get("inventory", []))
    inventory.append(
        {
            "id": reward["id"],
            "name": _reward_inventory_name(reward),
            "type": reward["type"],
            "quantity": amount,
            "description": str(reward.get("description", "")).strip(),
            "source_reward_id": reward["id"],
            "source_node_id": str(reward.get("node_id", "")).strip(),
        }
    )
    player["inventory"] = inventory
    reward["inventory_item_id"] = reward["id"]
    return player, ""


def _reward_inventory_name(reward: dict[str, Any]) -> str:
    """奖励缺少独立名称时，用说明开头生成可读背包名。"""
    description = str(reward.get("description", "")).strip()
    if description:
        return description.split("：", 1)[0].split(":", 1)[0].strip()
    return str(reward["id"]).replace("_", " ")
