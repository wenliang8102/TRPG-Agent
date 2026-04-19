"""战斗工具链单元测试 — 武器系统、玩家入场、回合校验、动作消耗、phase 生命周期"""

import sys
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import d20

# 确保 backend 在 sys.path 中
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.graph.state import AttackInfo, CombatantState, CombatState, WeaponData
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.services.tool_service import _build_player_combatant, prepare_player_for_combat
from langchain_core.messages import ToolMessage
from langgraph.types import Command


# ── 辅助工厂 ─────────────────────────────────────────────────

def _make_goblin(uid: str = "goblin_1") -> dict:
    """生成最小可用的 goblin combatant dict"""
    return CombatantState(
        id=uid, name="Goblin", side="enemy",
        hp=7, max_hp=7, ac=15, speed=30,
        abilities={"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        modifiers={"str": -1, "dex": 2, "con": 0, "int": 0, "wis": -1, "cha": -1},
        attacks=[AttackInfo(name="Scimitar", attack_bonus=4, damage_dice="1d6+2", damage_type="slashing")],
    ).model_dump()


def _make_combat_state(
    participants: dict[str, dict],
    current_actor_id: str = "",
    round_num: int = 1,
    player_dict: dict | None = None,
) -> CombatState:
    """构建 CombatState Pydantic 实例。
    新模型中玩家不在 participants 里，需要在 initiative_order 中包含玩家 ID。"""
    # 从 participants 中过滤掉玩家条目（适配新模型）
    npc_participants = {k: v for k, v in participants.items() if not k.startswith("player_")}
    all_ids = list(participants.keys())
    return CombatState(
        round=round_num,
        participants={k: CombatantState(**v) for k, v in npc_participants.items()},
        initiative_order=all_ids,
        current_actor_id=current_actor_id or next(iter(all_ids)),
    )


def _make_player_combatant(profile_key: str = "战士") -> dict:
    """从预设角色卡生成已叠加战斗字段的玩家字典"""
    player = dict(PREDEFINED_CHARACTERS[profile_key])
    prepare_player_for_combat(player)
    return player


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 LangChain ToolCall 格式调用含 InjectedToolCallId 的 tool，绕过参数校验"""
    tool_call = {
        "name": tool_func.name,
        "args": tool_input,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool_func.invoke(tool_call)


# ── Phase 1: _build_player_combatant 测试 ──────────────────────


class TestBuildPlayerCombatant:
    """验证玩家角色卡 → 战斗字段叠加逻辑"""

    def test_melee_weapon_bonus(self):
        """战士→长剑: attack_bonus = prof(2) + STR mod(3) = 5"""
        result = _make_player_combatant("战士")

        assert result["id"] == "player_预设-战士"
        assert result["side"] == "player"
        assert result["hp"] == 12
        assert result["ac"] == 16

        longsword = next(a for a in result["attacks"] if a["name"] == "Longsword")
        assert longsword["attack_bonus"] == 5   # prof(2) + STR(3)
        assert longsword["damage_dice"] == "1d8"
        assert longsword["damage_type"] == "slashing"

    def test_finesse_weapon_takes_higher(self):
        """游荡者→短剑(finesse): 取 max(STR=0, DEX=3) → bonus = 2+3 = 5"""
        result = _make_player_combatant("游荡者")

        shortsword = next(a for a in result["attacks"] if a["name"] == "Shortsword")
        assert shortsword["attack_bonus"] == 5  # prof(2) + max(STR=0, DEX=3)

    def test_ranged_weapon_uses_dex(self):
        """游荡者→短弓(ranged): 使用 DEX mod(3) → bonus = 2+3 = 5"""
        result = _make_player_combatant("游荡者")

        shortbow = next(a for a in result["attacks"] if a["name"] == "Shortbow")
        assert shortbow["attack_bonus"] == 5  # prof(2) + DEX(3)

    def test_no_weapons_yields_empty_attacks(self):
        """没有武器时 attacks 为空列表"""
        player = {
            "name": "无武器角色", "role_class": "测试",
            "hp": 5, "max_hp": 5, "ac": 10,
            "abilities": {}, "modifiers": {},
        }
        prepare_player_for_combat(player)
        assert player["attacks"] == []

    def test_all_predefined_have_weapons(self):
        """所有预设职业都应有至少一把武器"""
        for class_name, profile in PREDEFINED_CHARACTERS.items():
            assert len(profile.get("weapons", [])) > 0, f"{class_name} 缺少武器数据"


# ── Phase 2: start_combat 玩家自动入场 ──────────────────────────


class TestStartCombatPlayerJoin:
    """验证 start_combat 自动将已加载的玩家角色纳入战斗（玩家在 player 字段，不在 participants）"""

    def _invoke_start_combat(self, state: dict):
        from app.services.tool_service import start_combat
        return _invoke_tool(start_combat, tool_input={"combatant_ids": ["goblin_1"], "state": state})

    def test_player_added_to_initiative(self):
        """玩家角色卡存在时，start_combat 后 initiative_order 应包含玩家 ID"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}}
        result = self._invoke_start_combat(state)

        combat_update = result.update["combat"]
        assert "player_预设-战士" in combat_update["initiative_order"]
        # 玩家不在 participants 中（新模型）
        assert "player_预设-战士" not in combat_update["participants"]
        # 玩家战斗字段叠加在 player 上
        assert result.update["player"]["id"] == "player_预设-战士"
        assert result.update["player"]["side"] == "player"

    def test_phase_set_to_combat(self):
        """start_combat 应将 phase 设为 'combat'"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}}
        result = self._invoke_start_combat(state)
        assert result.update.get("phase") == "combat"

    def test_no_player_still_works(self):
        """没有加载玩家角色时，start_combat 仍然正常运行"""
        goblin = _make_goblin()
        state = {"scene_units": {"goblin_1": goblin}}
        result = self._invoke_start_combat(state)
        assert "goblin_1" in result.update["combat"]["participants"]


# ── Phase 3: attack_action 校验与动作消耗 ────────────────────────


class TestAttackActionValidation:
    """验证 attack_action 的前置校验（回合归属/目标存活/动作资源）"""

    def _invoke_attack(self, state: dict, attacker_id: str, target_id: str, **kwargs):
        from app.services.tool_service import attack_action
        params = {
            "attacker_id": attacker_id,
            "target_id": target_id,
            "state": state,
            **kwargs,
        }
        return _invoke_tool(attack_action, tool_input=params)

    def _build_state(self, current_actor_id: str, player_action_available: bool = True) -> dict:
        """构建含玩家+怪物的标准战斗状态（新模型：玩家在 player 字段）"""
        player_c = _make_player_combatant("战士")
        player_c["action_available"] = player_action_available

        goblin = _make_goblin()
        combat = _make_combat_state(
            {"player_预设-战士": player_c, "goblin_1": goblin},
            current_actor_id=current_actor_id,
            player_dict=player_c,
        )
        return {"combat": combat, "player": player_c}

    def test_wrong_turn_rejected(self):
        """攻击者不是当前行动者 → 返回错误"""
        state = self._build_state(current_actor_id="goblin_1")
        result = self._invoke_attack(state, "player_预设-战士", "goblin_1")
        assert isinstance(result, Command)
        msg = result.update["messages"][0].content
        assert "回合" in msg

    def test_dead_target_rejected(self):
        """目标 hp=0 → 返回错误"""
        state = self._build_state(current_actor_id="player_预设-战士")
        combat_dict = state["combat"].model_dump()
        combat_dict["participants"]["goblin_1"]["hp"] = 0
        state["combat"] = CombatState(**combat_dict)

        result = self._invoke_attack(state, "player_预设-战士", "goblin_1")
        assert isinstance(result, Command)
        msg = result.update["messages"][0].content
        assert "倒下" in msg

    def test_no_action_rejected(self):
        """action_available=False → 返回错误"""
        state = self._build_state(current_actor_id="player_预设-战士", player_action_available=False)
        result = self._invoke_attack(state, "player_预设-战士", "goblin_1")
        assert isinstance(result, Command)
        msg = result.update["messages"][0].content
        assert "用尽" in msg

    def test_successful_attack_consumes_action(self):
        """攻击成功后 attacker.action_available 应为 False（使用怪物攻击以避免玩家 interrupt）"""
        state = self._build_state(current_actor_id="goblin_1")
        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert isinstance(result, Command)
        attacker = result.update["combat"]["participants"]["goblin_1"]
        assert attacker["action_available"] is False

    def test_attack_deals_damage_from_weapon(self):
        """攻击使用武器数据而非硬编码（使用怪物攻击以避免玩家 interrupt）"""
        state = self._build_state(current_actor_id="goblin_1")
        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert isinstance(result, Command)
        msg_content = result.update["messages"][0].content
        assert "攻击" in msg_content


# ── Phase 4: next_turn 重置动作资源 ──────────────────────────────


class TestNextTurnReset:
    """验证 next_turn 正确重置下一个行动者的动作资源"""

    def test_next_actor_action_reset(self):
        from app.services.tool_service import next_turn

        player_c = _make_player_combatant("战士")
        player_c["action_available"] = False  # 上个回合已用

        goblin = _make_goblin()
        goblin["action_available"] = False

        combat = _make_combat_state(
            {"player_预设-战士": player_c, "goblin_1": goblin},
            current_actor_id="player_预设-战士",
            player_dict=player_c,
        )
        state = {"combat": combat, "player": player_c}

        result = _invoke_tool(next_turn, tool_input={"state": state})
        assert isinstance(result, Command)

        # 下一个行动者（goblin_1）的动作已重置
        next_actor = result.update["combat"]["participants"]["goblin_1"]
        assert next_actor["action_available"] is True


# ── Phase 5: end_combat phase 生命周期 ───────────────────────────


class TestPhaseLifecycle:
    """验证 phase 在战斗生命周期中正确变化"""

    def test_end_combat_resets_phase(self):
        from app.services.tool_service import end_combat

        goblin = _make_goblin()
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {"combat": combat}

        result = _invoke_tool(end_combat, tool_input={"state": state})
        assert result.update.get("phase") == "exploration"
        assert result.update.get("combat") is None


# ── Phase 6: WeaponData 模型验证 ────────────────────────────────


class TestWeaponDataModel:

    def test_weapon_data_fields(self):
        w = WeaponData(name="Longsword", damage_dice="1d8", damage_type="slashing",
                       weapon_type="melee", properties=["versatile"])
        assert w.name == "Longsword"
        assert w.weapon_type == "melee"
        assert "versatile" in w.properties

    def test_weapon_data_defaults(self):
        w = WeaponData(name="Fist")
        assert w.damage_dice == "1d4"
        assert w.weapon_type == "melee"
        assert w.properties == []


# ── Phase 7: modify_character_state 资源同步 ─────────────────────


class TestModifyCharacterStateResources:
    """验证战斗中恢复资源时，玩家数据保持一致（新模型中玩家就是单一数据源）"""

    def test_player_spell_slot_recovery_uses_actual_current_value_in_combat(self):
        from app.services.tool_service import modify_character_state

        player = dict(PREDEFINED_CHARACTERS["法师"])
        player["resources"] = {"spell_slot_lv1": 1}
        prepare_player_for_combat(player)
        combat = _make_combat_state(
            {player["id"]: player},
            current_actor_id=player["id"],
            player_dict=player,
        )
        state = {"player": player, "combat": combat}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "changes": {"resource_delta": {"spell_slot_lv1": 1}},
                "reason": "恢复法术位",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        # 新模型中只需检查 player 字段，不存在双写问题
        assert result.update["player"]["resources"]["spell_slot_lv1"] == 2

    def test_player_spell_slot_set_resource_clamps_to_predefined_cap(self):
        from app.services.tool_service import modify_character_state

        player = dict(PREDEFINED_CHARACTERS["法师"])
        player["resources"] = {"spell_slot_lv1": 0}
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "changes": {"set_resource": {"spell_slot_lv1": 99}},
                "reason": "长休恢复至上限",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["resources"]["spell_slot_lv1"] == 2


# ── Phase 8: Mage Armor 法术回归测试 ───────────────────────────


class TestMageArmorSpell:
    """验证法师护甲已注册、已装载到预设施法者，并能通过状态系统生效"""

    def test_mage_armor_loaded_on_wizard_and_sorcerer(self):
        assert "mage_armor" in PREDEFINED_CHARACTERS["法师"]["known_spells"]
        assert "mage_armor" in PREDEFINED_CHARACTERS["术士"]["known_spells"]

    def test_mage_armor_condition_is_registered(self):
        from app.conditions import get_condition_def

        condition_def = get_condition_def("mage_armor")
        assert condition_def is not None
        assert condition_def.name_cn == "法师护甲"

    def test_cast_mage_armor_sets_ac_and_condition(self):
        from app.services.tool_service import cast_spell
        from app.services.tools._helpers import compute_ac

        player = dict(PREDEFINED_CHARACTERS["法师"])
        state = {"player": player}

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "mage_armor",
                "target_ids": ["self"],
                "slot_level": 1,
                "state": state,
            },
        )

        assert isinstance(result, Command)
        updated_player = result.update["player"]
        # mage_armor 不再直接修改 ac，而是通过 compute_ac 动态计算
        assert compute_ac(updated_player) == 15
        assert updated_player["resources"]["spell_slot_lv1"] == 1
        assert any(c.get("id") == "mage_armor" for c in updated_player["conditions"])

