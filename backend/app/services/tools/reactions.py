"""反应系统调度器 — 声明式触发 + 通用执行

每种反应法术在 SPELL_DEF 中声明 `reaction_trigger` 字段，
调度器根据触发类型自动发现可用反应、构建 interrupt payload、执行选择。

触发类型 (可扩展):
  on_hit         — 被攻击命中时 (Shield)
  on_enemy_cast  — 敌方施法时 (Counterspell, 未来)
  on_leave_reach — 离开触及范围 (Opportunity Attack, 未来)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ReactionTrigger = Literal["on_hit", "on_enemy_cast", "on_leave_reach"]


@dataclass
class ReactionResult:
    """反应执行结果"""
    lines: list[str] = field(default_factory=list)
    used: bool = False        # 是否实际消耗了反应机会
    modifies_ac: bool = False # 是否改变了目标 AC（用于攻击重判定）
    blocked_action: bool = False  # 是否完全阻止了触发动作（如 Counterspell）


# ── 可用反应发现 ──────────────────────────────────────────────

def get_available_reactions(
    actor: dict,
    trigger: ReactionTrigger,
    context: dict,
) -> list[dict]:
    """收集 actor 对指定触发类型可用的所有反应法术。
    返回 [{spell_id, name_cn, min_slot, description}] 列表。"""
    from app.spells import SPELL_REGISTRY
    from app.services.tools._helpers import get_condition_action_block_reason

    if not actor.get("reaction_available", False):
        return []
    if get_condition_action_block_reason(actor, "reaction"):
        return []

    known = set(actor.get("known_spells", []))
    resources = actor.get("resources", {})
    result: list[dict] = []

    for spell_id in known:
        mod = SPELL_REGISTRY.get(spell_id)
        if not mod:
            continue
        spell_def = mod.SPELL_DEF
        if spell_def.get("casting_time") != "reaction":
            continue
        # 声明式 trigger 匹配
        if spell_def.get("reaction_trigger") != trigger:
            continue

        min_level = spell_def["level"]
        # 找到最低可用法术位
        for lv in range(min_level, 10):
            slot_key = f"spell_slot_lv{lv}"
            pact_key = f"pact_magic_lv{lv}"
            if resources.get(slot_key, 0) > 0 or resources.get(pact_key, 0) > 0:
                result.append({
                    "spell_id": spell_id,
                    "name_cn": spell_def["name_cn"],
                    "min_slot": lv,
                    "description": spell_def.get("description", ""),
                })
                break

    return result


# ── Interrupt Payload 构建 ────────────────────────────────────

def build_interrupt_payload(
    trigger: ReactionTrigger,
    context: dict,
    available: list[dict],
) -> dict:
    """构建标准化 interrupt 数据，前端 ActionPanel 统一消费。"""
    return {
        "type": "reaction_prompt",
        "trigger": trigger,
        "available_reactions": available,
        **context,  # 各 trigger 的上下文字段直接展开
    }


# ── 玩家反应执行 ──────────────────────────────────────────────

def execute_player_reaction(
    player: dict,
    choice: dict,
    context: dict,
) -> ReactionResult:
    """执行玩家选择的反应法术：消耗法术位 + 调用 execute + 标记反应已用。"""
    from app.spells import get_spell_module
    from app.services.tools._helpers import consume_spell_slot, get_condition_action_block_reason

    spell_id = choice.get("spell_id")
    if not spell_id:
        return ReactionResult()

    if block_reason := get_condition_action_block_reason(player, "reaction"):
        return ReactionResult(lines=[block_reason])

    mod = get_spell_module(spell_id)
    if not mod:
        return ReactionResult(lines=[f"未知反应法术: {spell_id}"])

    spell_def = mod.SPELL_DEF
    min_lv = spell_def["level"]
    chosen_slot = choice.get("slot_level", min_lv)

    # 消耗法术位
    resources = player.get("resources", {})
    consume_key = consume_spell_slot(resources, chosen_slot)
    if consume_key:
        resources[consume_key] -= 1

    # 执行法术
    result = mod.execute(caster=player, targets=[player], slot_level=chosen_slot)

    # 标记反应已用
    player["reaction_available"] = False

    remaining = resources.get(consume_key, "?") if consume_key else "?"
    lines = list(result.get("lines", []))
    lines.append(f"（剩余{chosen_slot}环法术位: {remaining}）")

    # 根据法术学派判定是否影响 AC（防护系反应法术通常修改 AC）
    modifies_ac = spell_def.get("school") == "abjuration"

    return ReactionResult(lines=lines, used=True, modifies_ac=modifies_ac)


# ── 怪物 AI 自动反应 ──────────────────────────────────────────

def resolve_npc_reaction(
    npc: dict,
    trigger: ReactionTrigger,
    context: dict,
) -> ReactionResult:
    """怪物/NPC 自动决策反应。当前策略：有可用反应就使用（最简策略）。"""
    available = get_available_reactions(npc, trigger, context)
    if not available:
        return ReactionResult()

    # 简单策略：选择第一个可用反应
    chosen = available[0]
    return execute_player_reaction(npc, {
        "spell_id": chosen["spell_id"],
        "slot_level": chosen["min_slot"],
    }, context)
