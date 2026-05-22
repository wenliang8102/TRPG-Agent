"""战斗 XP 结算 — 只根据本场战斗快照发奖，避免复盘历史文本。"""

from __future__ import annotations

from typing import Any

from app.calculation.experience import xp_from_cr


def defeated_enemy_ids(participants: dict[str, dict[str, Any]], defeated_unit_ids: set[str]) -> set[str]:
    """HP 归零自动算击败；投降、俘虏、驱散等由工具参数显式补充。"""
    defeated: set[str] = set()
    for unit_id, unit in participants.items():
        if unit.get("side") != "enemy":
            continue
        if unit.get("hp", 0) <= 0 or unit_id in defeated_unit_ids:
            defeated.add(unit_id)
    return defeated


def grant_combat_xp(
    player: dict[str, Any] | None,
    participants: dict[str, dict[str, Any]],
    defeated_unit_ids: set[str],
) -> dict[str, Any] | None:
    """把已击败敌人的 XP 加给玩家；同一战斗结算 id 防止重复发放。"""
    if not player:
        return None

    defeated = defeated_enemy_ids(participants, defeated_unit_ids)
    awards = [_unit_xp_award(unit_id, participants[unit_id]) for unit_id in sorted(defeated)]
    awards = [award for award in awards if award["xp"] > 0]
    if not awards:
        return None

    claimed = set(player.get("claimed_combat_xp_ids", []))
    pending_awards = [award for award in awards if award["id"] not in claimed]
    if not pending_awards:
        return None

    total_xp = sum(award["xp"] for award in pending_awards)
    previous_xp = int(player.get("xp", 0) or 0)
    player["xp"] = previous_xp + total_xp
    player["claimed_combat_xp_ids"] = [*player.get("claimed_combat_xp_ids", []), *[award["id"] for award in pending_awards]]
    player.setdefault("combat_xp_log", []).append(
        {
            "total_xp": total_xp,
            "previous_xp": previous_xp,
            "current_xp": player["xp"],
            "awards": pending_awards,
        }
    )
    return {
        "total_xp": total_xp,
        "previous_xp": previous_xp,
        "current_xp": player["xp"],
        "awards": pending_awards,
    }


def _unit_xp_award(unit_id: str, unit: dict[str, Any]) -> dict[str, Any]:
    """单位快照优先使用生成时固化的 XP，缺失时回退到 CR 表。"""
    cr = str(unit.get("challenge_rating", "0"))
    xp = int(unit.get("xp_value") or xp_from_cr(cr))
    return {
        "id": f"combat:{unit_id}",
        "unit_id": unit_id,
        "name": str(unit.get("name", unit_id)),
        "challenge_rating": cr,
        "xp": xp,
    }

