"""战斗工具链单元测试 — 武器系统、玩家入场、回合校验、动作消耗、phase 生命周期"""

import sys
import re
import copy
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
from app.conditions._base import build_condition_extra, create_condition
from app.services.tools._helpers import prepare_character_for_combat, prepare_player_for_combat
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command


# ── 辅助工厂 ─────────────────────────────────────────────────

def _make_goblin(uid: str = "goblin_1") -> dict:
    """生成最小可用的 goblin combatant dict"""
    return CombatantState(
        id=uid, name="Goblin", side="enemy",
        hp=7, max_hp=7, ac=15, speed=30,
        abilities={"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        modifiers={"str": -1, "dex": 2, "con": 0, "int": 0, "wis": -1, "cha": -1},
        attacks=[AttackInfo(name="Scimitar", attack_bonus=4, damage_dice="1d6+2", damage_type="slashing", reach_feet=5)],
        monster_slug="goblin",
        challenge_rating="1/4",
        xp_value=50,
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
    player_id = player_dict.get("id") if player_dict else None
    npc_participants = {
        k: v for k, v in participants.items()
        if k != player_id and v.get("side") != "player"
    }
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


def _make_space_state(unit_ids: list[str]) -> dict:
    """构建最小战斗地图，让 start_combat 的空间前置条件显式满足。"""
    return {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
        "placements": {
            unit_id: {"unit_id": unit_id, "map_id": "map_1", "position": {"x": index * 10, "y": 0}}
            for index, unit_id in enumerate(unit_ids)
        },
    }


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 LangChain ToolCall 格式调用含 InjectedToolCallId 的 tool，绕过参数校验"""
    tool_call = {
        "name": tool_func.name,
        "args": tool_input,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool_func.invoke(tool_call)


def test_load_character_profile_uses_custom_name_as_player_id():
    """加载角色时玩家名字就是稳定单位 ID，职业只保存在 role_class。"""
    from app.services.tools.character_tools import load_character_profile

    result = _invoke_tool(
        load_character_profile,
        tool_input={"character_name": "温良", "role_class": "法师"},
    )

    player = result.update["player"]
    assert player["id"] == "温良"
    assert player["name"] == "温良"
    assert player["role_class"] == "法师"
    assert "预设" not in result.update["messages"][0].content


    # ── Phase 1: prepare_player_for_combat 测试 ──────────────────────


class TestBuildPlayerCombatant:
    """验证玩家角色卡 → 战斗字段叠加逻辑"""

    def test_melee_weapon_bonus(self):
        """战士→长剑: 命中与伤害都加入 STR 调整值。"""
        result = _make_player_combatant("战士")

        assert result["id"] == "战士"
        assert result["name"] == "战士"
        assert result["side"] == "player"
        assert result["hp"] == 12
        assert result["ac"] == 16

        longsword = next(a for a in result["attacks"] if a["name"] == "Longsword")
        assert longsword["attack_bonus"] == 5   # prof(2) + STR(3)
        assert longsword["damage_dice"] == "1d8+3"
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
        assert shortbow["damage_dice"] == "1d6+3"
        assert shortbow["normal_range_feet"] == 80
        assert shortbow["long_range_feet"] == 320

    def test_light_crossbow_uses_registry_range(self):
        """轻弩是开局友方常用远程武器，必须从武器表继承 80/320 尺射程。"""
        from app.allies.profiles import get_ally_profile

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")

        light_crossbow = next(a for a in ally["attacks"] if a["name"] == "Light Crossbow")
        assert light_crossbow["attack_bonus"] == 3
        assert light_crossbow["damage_dice"] == "1d8+1"
        assert light_crossbow["normal_range_feet"] == 80
        assert light_crossbow["long_range_feet"] == 320

    def test_thrown_weapon_uses_strength_and_range_from_registry(self):
        """标枪按近战投掷武器处理：STR 攻击/伤害，同时带 30/120 尺射程。"""
        result = _make_player_combatant("野蛮人")

        javelin = next(a for a in result["attacks"] if a["name"] == "Javelin")
        assert javelin["attack_bonus"] == 5
        assert javelin["damage_dice"] == "1d6+3"
        assert javelin["normal_range_feet"] == 30
        assert javelin["long_range_feet"] == 120

    def test_lost_mine_named_weapon_applies_magic_bonus(self):
        """模组命名武器从装备表继承基础武器，并叠加 +1 魔法武器加值。"""
        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["weapons"] = [{"name": "Talon"}]
        prepare_player_for_combat(player)

        talon = player["attacks"][0]
        assert talon["attack_bonus"] == 6
        assert talon["damage_dice"] == "1d8+4"

    def test_no_weapons_yields_empty_attacks(self):
        """没有武器时 attacks 为空列表"""
        player = {
            "name": "无武器角色", "role_class": "测试",
            "hp": 5, "max_hp": 5, "ac": 10,
            "abilities": {}, "modifiers": {},
        }
        prepare_player_for_combat(player)
        assert player["attacks"] == []

    def test_custom_chinese_name_is_stable_player_id(self):
        """玩家中文名直接作为单位 ID，职业仍保留为 role_class。"""
        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["name"] = "温良"
        prepare_player_for_combat(player)

        assert player["id"] == "温良"
        assert player["name"] == "温良"
        assert player["role_class"] == "法师"

    def test_all_predefined_have_weapons(self):
        """所有预设职业都应有至少一把武器"""
        for class_name, profile in PREDEFINED_CHARACTERS.items():
            assert len(profile.get("weapons", [])) > 0, f"{class_name} 缺少武器数据"


class TestFighterArchetype:
    """验证战士 3 级武术范型只写入当前版本支持的字段。"""

    @pytest.mark.parametrize(
        ("archetype", "expected_features"),
        [
            ("champion", ["improved_critical"]),
            ("battle_master", ["combat_superiority", "student_of_war"]),
            ("eldritch_knight", ["eldritch_knight_spellcasting", "weapon_bond"]),
        ],
    )
    def test_choose_fighter_archetype_records_features(self, archetype, expected_features):
        """三种范型都应记录范型名，并授予 3 级立即获得的职业特性。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["level"] = 3
        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_fighter_archetype",
                "payload": {"archetype": archetype},
                "state": {"player": player},
            },
        )
        updated = result.update["player"]

        assert updated["fighter_archetype"] == archetype
        for feature in expected_features:
            assert feature in updated["class_features"]

    def test_battle_master_records_level_3_resources(self):
        """战斗大师 3 级只记录卓越骰、战技槽和工具熟练占位，不执行战技。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["level"] = 3
        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_fighter_archetype",
                "payload": {"archetype": "battle_master"},
                "state": {"player": player},
            },
        )
        updated = result.update["player"]

        assert updated["resources"]["superiority_dice"] == 4
        assert updated["resource_caps"]["superiority_dice"] == 4
        assert updated["superiority_die"] == "1d8"
        assert updated["maneuvers_known_count"] == 3
        assert updated["maneuvers"] == []
        assert updated["maneuver_save_ability"] == "str"
        assert updated["maneuver_save_dc"] == 13
        assert updated["artisan_tool_proficiency"] == ""

    def test_eldritch_knight_records_level_3_spellcasting(self):
        """奥法骑士 3 级只记录施法字段、默认法术和武器联结占位。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["level"] = 3
        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_fighter_archetype",
                "payload": {"archetype": "eldritch_knight"},
                "state": {"player": player},
            },
        )
        updated = result.update["player"]

        assert updated["spellcasting_ability"] == "int"
        assert updated["eldritch_knight_cantrips_known"] == 2
        assert updated["eldritch_knight_spells_known"] == 3
        assert updated["spell_save_dc"] == 10
        assert updated["spell_attack_bonus"] == 2
        assert updated["resources"]["spell_slot_lv1"] == 2
        assert updated["resource_caps"]["spell_slot_lv1"] == 2
        assert updated["bonded_weapons"] == []
        assert updated["bonded_weapon_limit"] == 2
        assert "fire_bolt" in updated["known_cantrips"]
        assert "ray_of_frost" in updated["known_cantrips"]
        assert "shield" in updated["known_spells"]
        assert "magic_missile" in updated["known_spells"]
        assert "burning_hands" in updated["known_spells"]

    def test_eldritch_knight_level_4_syncs_spellcasting_table(self):
        """奥法骑士升到 4 级后，应获得 4 个已知法术和 3 个 1 环法术位。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player.update({
            "level": 3,
            "xp": 2700,
            "fighter_archetype": "eldritch_knight",
            "class_features": [
                "fighting_style",
                "second_wind",
                "action_surge",
                "eldritch_knight_spellcasting",
                "weapon_bond",
            ],
            "known_cantrips": ["fire_bolt", "ray_of_frost"],
            "known_spells": ["shield", "magic_missile", "burning_hands"],
            "resources": {"second_wind_uses": 1, "action_surge_uses": 1, "spell_slot_lv1": 2},
            "resource_caps": {"second_wind_uses": 1, "action_surge_uses": 1, "spell_slot_lv1": 2},
        })

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "level_up",
                "state": {"player": player},
            },
        )
        updated = result.update["player"]

        assert updated["level"] == 4
        assert updated["eldritch_knight_cantrips_known"] == 2
        assert updated["eldritch_knight_spells_known"] == 4
        assert updated["resources"]["spell_slot_lv1"] == 3
        assert updated["resource_caps"]["spell_slot_lv1"] == 3
        assert "thunderwave" in updated["known_spells"]
        assert "ability_score_improvement" in updated["class_features"]

    def test_eldritch_knight_level_5_keeps_level_5_spellcasting_table(self):
        """奥法骑士 5 级仍是 2 戏法、4 已知法术、3 个 1 环法术位。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player.update({
            "level": 4,
            "xp": 6500,
            "fighter_archetype": "eldritch_knight",
            "class_features": [
                "fighting_style",
                "second_wind",
                "action_surge",
                "ability_score_improvement",
                "eldritch_knight_spellcasting",
                "weapon_bond",
            ],
            "known_cantrips": ["fire_bolt", "ray_of_frost"],
            "known_spells": ["shield", "magic_missile", "burning_hands", "thunderwave"],
            "resources": {"second_wind_uses": 1, "action_surge_uses": 1, "spell_slot_lv1": 3},
            "resource_caps": {"second_wind_uses": 1, "action_surge_uses": 1, "spell_slot_lv1": 3},
        })

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "level_up",
                "state": {"player": player},
            },
        )
        updated = result.update["player"]

        assert updated["level"] == 5
        assert updated["eldritch_knight_cantrips_known"] == 2
        assert updated["eldritch_knight_spells_known"] == 4
        assert updated["resources"]["spell_slot_lv1"] == 3
        assert updated["resource_caps"]["spell_slot_lv1"] == 3
        assert "extra_attack" in updated["class_features"]

    def test_champion_crit_on_natural_19(self):
        """勇士的精通重击应把武器暴击阈值压到 19。"""
        from app.services.tools._helpers import roll_attack_hit

        attacker = _make_player_combatant("战士")
        attacker["class_features"] = ["improved_critical"]
        target = _make_goblin()

        with patch("app.services.tools._helpers._get_natural_d20", return_value=19):
            roll_info = roll_attack_hit(attacker, target)

        assert roll_info["hit"] is True
        assert roll_info["crit"] is True

    def test_battle_master_does_not_crit_on_natural_19(self):
        """战斗大师不应继承勇士的 19-20 暴击。"""
        from app.services.tools._helpers import roll_attack_hit

        attacker = _make_player_combatant("战士")
        attacker["class_features"] = ["combat_superiority", "student_of_war"]
        target = _make_goblin()

        with patch("app.services.tools._helpers._get_natural_d20", return_value=19):
            roll_info = roll_attack_hit(attacker, target)

        assert roll_info["crit"] is False


# ── Phase 2: start_combat 玩家自动入场 ──────────────────────────


class TestStartCombatPlayerJoin:
    """验证 start_combat 自动将已加载的玩家角色纳入战斗（玩家在 player 字段，不在 participants）"""

    def _invoke_start_combat(self, state: dict, surprised_ids: list[str] | str | None = None):
        from app.services.tools.combat_tools import start_combat
        tool_input = {"combatant_ids": ["goblin_1"], "state": state}
        if surprised_ids is not None:
            tool_input["surprised_ids"] = surprised_ids
        return _invoke_tool(start_combat, tool_input=tool_input)

    def test_player_added_to_initiative(self):
        """玩家角色卡存在时，start_combat 后 initiative_order 应包含玩家 ID"""
        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良"}
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["温良", "goblin_1"])}
        result = self._invoke_start_combat(state)

        combat_update = result.update["combat"]
        assert "温良" in combat_update["initiative_order"]
        # 玩家不在 participants 中（新模型）
        assert "温良" not in combat_update["participants"]
        # 玩家战斗字段叠加在 player 上
        assert result.update["player"]["id"] == "温良"
        assert result.update["player"]["side"] == "player"

    def test_active_combat_start_includes_start_tool_call(self):
        """活跃战斗窗口从 start_combat 的 AI tool_call 开始，便于长上下文裁剪保护完整工具链。"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {
            "player": player,
            "scene_units": {"goblin_1": goblin},
            "space": _make_space_state(["player_预设-战士", "goblin_1"]),
            "messages": [
                HumanMessage(content="我靠近狼。"),
                AIMessage(
                    content="进入战斗。",
                    tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["goblin_1"]}, "id": "call_start"}],
                ),
            ],
        }

        result = self._invoke_start_combat(state)

        assert result.update["active_combat_message_start"] == 1

    def test_phase_set_to_combat(self):
        """start_combat 应将 phase 设为 'combat'"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["player_预设-战士", "goblin_1"])}
        result = self._invoke_start_combat(state)
        assert result.update.get("phase") == "combat"

    def test_no_player_still_works(self):
        """没有加载玩家角色时，start_combat 仍然正常运行"""
        goblin = _make_goblin()
        state = {"scene_units": {"goblin_1": goblin}, "space": _make_space_state(["goblin_1"])}
        result = self._invoke_start_combat(state)
        assert "goblin_1" in result.update["combat"]["participants"]

    def test_surprised_player_rolls_initiative_with_disadvantage_only(self):
        """新版突袭只影响先攻劣势，不禁用首回合行动或反应。"""
        class FakeRoll:
            def __init__(self, total: int, text: str):
                self.total = total
                self.text = text

            def __str__(self) -> str:
                return self.text

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良"}
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["温良", "goblin_1"])}
        rolled_exprs: list[str] = []

        def fake_roll(expr: str):
            rolled_exprs.append(expr)
            if expr.startswith("2d20kl1"):
                return FakeRoll(5, "2d20kl1+2 = 5")
            return FakeRoll(14, "1d20+2 = 14")

        with patch("app.services.tools.combat_tools.d20.roll", side_effect=fake_roll):
            result = self._invoke_start_combat(state, surprised_ids=["player"])

        assert any(expr.startswith("2d20kl1") for expr in rolled_exprs)
        assert result.update["player"]["surprised"] is True
        assert result.update["player"]["action_available"] is True
        assert result.update["player"]["reaction_available"] is True
        assert "突袭劣势" in result.update["messages"][0].content
        assert "突袭单位的先攻劣势已计入下列骰式" in result.update["messages"][0].content
        assert "不要自行重排或补骰" in result.update["messages"][0].content

    def test_start_combat_accepts_json_string_surprised_ids(self):
        """真实模型偶尔把 surprised_ids 列表序列化成字符串，开战工具应归一化后继续执行。"""
        class FakeRoll:
            def __init__(self, total: int, text: str):
                self.total = total
                self.text = text

            def __str__(self) -> str:
                return self.text

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良"}
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["温良", "goblin_1"])}
        rolled_exprs: list[str] = []

        def fake_roll(expr: str):
            rolled_exprs.append(expr)
            return FakeRoll(5 if expr.startswith("2d20kl1") else 14, expr)

        with patch("app.services.tools.combat_tools.d20.roll", side_effect=fake_roll):
            result = self._invoke_start_combat(state, surprised_ids='["player"]')

        assert any(expr.startswith("2d20kl1") for expr in rolled_exprs)
        assert result.update["player"]["surprised"] is True
        assert "combat" in result.update

    def test_start_combat_rejects_unknown_surprised_id(self):
        """突袭目标必须能解析到参战单位，避免静默忽略拼错 ID。"""
        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良"}
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["温良", "goblin_1"])}

        result = self._invoke_start_combat(state, surprised_ids=["missing_1"])

        assert "找不到被突袭单位" in result.update["messages"][0].content
        assert "combat" not in result.update

    def test_start_combat_requires_active_space_map(self):
        """战斗开始前必须先建立平面地图。"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {"player": player, "scene_units": {"goblin_1": goblin}}

        result = self._invoke_start_combat(state)

        assert "无法开始战斗" in result.update["messages"][0].content
        assert "平面地图" in result.update["messages"][0].content
        assert "combat" not in result.update

    def test_start_combat_requires_all_combatants_placed(self):
        """战斗开始前所有参战者都必须有落点。"""
        player = PREDEFINED_CHARACTERS["战士"]
        goblin = _make_goblin()
        state = {
            "player": player,
            "scene_units": {"goblin_1": goblin},
            "space": _make_space_state(["player_预设-战士"]),
        }

        result = self._invoke_start_combat(state)

        assert "goblin_1" in result.update["messages"][0].content
        assert "尚未放置" in result.update["messages"][0].content
        assert "combat" not in result.update

    def test_prepare_combat_start_submits_workflow_plan(self):
        """LLM 只提交入场与突袭裁量，正式先攻交给 workflow 节点。"""
        from app.services.tools.combat_tools import prepare_combat_start

        result = _invoke_tool(
            prepare_combat_start,
            tool_input={
                "combatant_ids": ["goblin_1"],
                "surprised_ids": ["player"],
                "map_plan": {"action": "create", "name": "伏击路段", "width": 150, "height": 120},
                "placements": [{"unit_id": "player", "x": 20, "y": 60}, {"unit_id": "goblin_1", "x": 70, "y": 40}],
                "reason": "地精藏在灌木中，玩家被伏击。",
            },
        )

        assert result.update["pending_combat_start"]["combatant_ids"] == ["goblin_1"]
        assert result.update["pending_combat_start"]["surprised_ids"] == ["player"]
        assert result.update["pending_combat_start"]["map_plan"]["name"] == "伏击路段"
        assert len(result.update["pending_combat_start"]["placements"]) == 2
        assert "开战计划" in result.update["messages"][0].content
        assert "落点数量: 2" in result.update["messages"][0].content
        assert result.update["messages"][0].additional_kwargs["hidden_from_ui"] is True


class TestAllySystem:
    """友方单位复用角色型规则，但由 Agent 控制行动。"""

    def test_spawn_ally_adds_character_like_scene_unit(self):
        """友方法师生成后应带武器、法术资源和反应资源。"""
        from app.services.tools.combat_tools import manage_scene_units

        result = _invoke_tool(
            manage_scene_units,
            tool_input={
                "action": "spawn_ally",
                "profile_id": "apprentice_wizard",
                "name": "伊莲",
                "unit_id": "ally_wizard",
                "state": {},
            },
        )

        ally = result.update["scene_units"]["ally_wizard"]
        assert ally["name"] == "伊莲"
        assert ally["side"] == "ally"
        assert ally["resources"]["spell_slot_lv1"] == 3
        assert "magic_missile" in ally["known_spells"]
        assert ally["reaction_available"] is True
        dagger = next(a for a in ally["attacks"] if a["name"] == "Dagger")
        assert dagger["attack_bonus"] == 4
        assert dagger["damage_dice"] == "1d4+2"
        assert dagger["normal_range_feet"] == 20

    def test_manage_scene_units_help_returns_skill_instructions(self):
        """场景单位聚合入口应能按需披露完整说明。"""
        from app.services.tools.combat_tools import manage_scene_units

        result = _invoke_tool(
            manage_scene_units,
            tool_input={"action": "help", "state": {}},
        )

        content = result.update["messages"][0].content
        assert "场景单位管理技能" in content
        assert "manage_scene_units" in content
        assert 'action="spawn_monsters"' in content

    def test_start_combat_prepares_ally_and_keeps_resources(self):
        """友方从场景入战后留在 participants，并保留角色资源。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.combat_tools import start_combat

        ally = get_ally_profile("apprentice_wizard")
        goblin = _make_goblin()
        state = {
            "scene_units": {"apprentice_wizard": ally, "goblin_1": goblin},
            "space": _make_space_state(["apprentice_wizard", "goblin_1"]),
        }

        result = _invoke_tool(
            start_combat,
            tool_input={"combatant_ids": ["apprentice_wizard", "goblin_1"], "state": state},
        )

        combat = result.update["combat"]
        prepared = combat["participants"]["apprentice_wizard"]
        assert prepared["side"] == "ally"
        assert prepared["resources"]["spell_slot_lv1"] == 3
        assert "Dagger" in [attack["name"] for attack in prepared["attacks"]]
        assert "apprentice_wizard" in combat["initiative_order"]

    def test_ally_wizard_casts_spell_and_consumes_slot(self):
        """战斗中的友方可以作为 caster_id 施法并消耗自己的法术位。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.spell_tools import cast_spell
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("apprentice_wizard"), side="ally")
        goblin = _make_goblin()
        state = {
            "combat": _make_combat_state({"apprentice_wizard": ally, "goblin_1": goblin}, current_actor_id="apprentice_wizard"),
            "space": _make_space_state(["apprentice_wizard", "goblin_1"]),
        }

        with patch("app.spells.magic_missile.d20.roll", return_value=d20.roll("2")):
            result = _invoke_tool(
                cast_spell,
                tool_input={
                    "caster_id": "apprentice_wizard",
                    "spell_id": "magic_missile",
                    "target_ids": ["goblin_1"],
                    "slot_level": 1,
                    "state": state,
                },
            )

        updated_ally = result.update["combat"]["participants"]["apprentice_wizard"]
        updated_goblin = result.update["combat"]["participants"]["goblin_1"]
        assert updated_ally["resources"]["spell_slot_lv1"] == 2
        assert updated_ally["action_available"] is False
        assert updated_goblin["hp"] < goblin["hp"]

    def test_ally_healer_can_restore_player(self):
        """友方治疗者能治疗玩家目标，并写回双方状态。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.spell_tools import cast_spell
        from app.services.tools._helpers import prepare_character_for_combat

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良", "hp": 0, "death_save_failures": 2}
        ally = prepare_character_for_combat(get_ally_profile("acolyte_healer"), side="ally")
        state = {
            "player": player,
            "combat": _make_combat_state({"acolyte_healer": ally, "温良": player}, current_actor_id="acolyte_healer", player_dict=player),
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
                "placements": {
                    "acolyte_healer": {"unit_id": "acolyte_healer", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "温良": {"unit_id": "温良", "map_id": "map_1", "position": {"x": 5, "y": 0}},
                },
            },
        }

        with patch("app.spells.cure_wounds.d20.roll", return_value=d20.roll("8")):
            result = _invoke_tool(
                cast_spell,
                tool_input={
                    "caster_id": "acolyte_healer",
                    "spell_id": "cure_wounds",
                    "target_ids": ["温良"],
                    "slot_level": 1,
                    "state": state,
                },
            )

        assert result.update["player"]["hp"] > 0
        assert result.update["player"]["death_save_failures"] == 0
        assert result.update["combat"]["participants"]["acolyte_healer"]["resources"]["spell_slot_lv1"] == 2

    def test_ally_auto_uses_shield_reaction_when_hit(self):
        """敌人命中非玩家友方时，友方反应由规则层自动执行。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.combat_tools import attack_action
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("apprentice_wizard"), side="ally")
        goblin = _make_goblin()
        state = {
            "combat": _make_combat_state({"goblin_1": goblin, "apprentice_wizard": ally}, current_actor_id="goblin_1"),
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
                "placements": {
                    "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "apprentice_wizard": {"unit_id": "apprentice_wizard", "map_id": "map_1", "position": {"x": 5, "y": 0}},
                },
            },
        }

        with patch("app.services.tools._helpers.d20.roll", return_value=d20.roll("16")):
            result = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": "apprentice_wizard",
                    "attack_name": "Scimitar",
                    "state": state,
                },
            )

        updated_ally = result.update["combat"]["participants"]["apprentice_wizard"]
        assert updated_ally["resources"]["spell_slot_lv1"] == 2
        assert updated_ally["reaction_available"] is False
        assert updated_ally["hp"] == ally["hp"]
        assert "护盾术" in result.update["messages"][0].content

    def test_ally_enters_death_save_state_when_dropped_to_zero(self):
        """友方倒地后进入死亡豁免流程，不被当作普通怪物尸体。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.combat_tools import attack_action
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("apprentice_wizard"), side="ally")
        ally["hp"] = 1
        ally["resources"]["spell_slot_lv1"] = 0
        goblin = _make_goblin()
        state = {
            "combat": _make_combat_state({"goblin_1": goblin, "apprentice_wizard": ally}, current_actor_id="goblin_1"),
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
                "placements": {
                    "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "apprentice_wizard": {"unit_id": "apprentice_wizard", "map_id": "map_1", "position": {"x": 5, "y": 0}},
                },
            },
        }

        with patch("app.services.tools._helpers.d20.roll", side_effect=[d20.roll("16"), d20.roll("4")]):
            result = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": "apprentice_wizard",
                    "attack_name": "Scimitar",
                    "state": state,
                },
            )

        updated_ally = result.update["combat"]["participants"]["apprentice_wizard"]
        assert updated_ally["hp"] == 0
        assert updated_ally["death_save_successes"] == 0
        assert updated_ally["death_save_failures"] == 0
        assert updated_ally["is_stable"] is False
        assert "死亡豁免 0 成功 / 0 失败" in result.update["messages"][0].content

    def test_use_healing_potion_feeds_dying_ally(self):
        """喂治疗药水可把 0 HP 友方拉起，并消耗动作。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.item_tools import manage_inventory
        from app.services.tools._helpers import prepare_character_for_combat

        actor = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        target = prepare_character_for_combat(get_ally_profile("apprentice_wizard"), side="ally")
        target["hp"] = 0
        target["death_save_failures"] = 2
        state = {
            "combat": _make_combat_state(
                {"fighter_companion": actor, "apprentice_wizard": target},
                current_actor_id="fighter_companion",
            ),
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
                "placements": {
                    "fighter_companion": {"unit_id": "fighter_companion", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "apprentice_wizard": {"unit_id": "apprentice_wizard", "map_id": "map_1", "position": {"x": 5, "y": 0}},
                },
            },
        }

        with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("6")):
            result = _invoke_tool(
                manage_inventory,
                tool_input={
                    "action": "use",
                    "item_id": "potion_of_healing",
                    "actor_id": "fighter_companion",
                    "target_id": "apprentice_wizard",
                    "mode": "feed",
                    "state": state,
                },
            )

        updated_actor = result.update["combat"]["participants"]["fighter_companion"]
        updated_target = result.update["combat"]["participants"]["apprentice_wizard"]
        assert updated_actor["action_available"] is False
        assert updated_actor["inventory"][0]["quantity"] == 1
        assert updated_target["hp"] == 6
        assert updated_target["is_stable"] is False
        assert updated_target["death_save_failures"] == 0
        assert "治疗药水恢复 6 HP" in result.update["messages"][0].content

    def test_use_healing_potion_feed_requires_touch_distance(self):
        """喂药需要接触目标，不能隔空治疗。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.item_tools import manage_inventory
        from app.services.tools._helpers import prepare_character_for_combat

        actor = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        target = prepare_character_for_combat(get_ally_profile("apprentice_wizard"), side="ally")
        target["hp"] = 0
        state = {
            "combat": _make_combat_state(
                {"fighter_companion": actor, "apprentice_wizard": target},
                current_actor_id="fighter_companion",
            ),
            "space": _make_space_state(["fighter_companion", "apprentice_wizard"]),
        }

        result = _invoke_tool(
            manage_inventory,
            tool_input={
                "action": "use",
                "item_id": "potion_of_healing",
                "actor_id": "fighter_companion",
                "target_id": "apprentice_wizard",
                "mode": "feed",
                "state": state,
            },
        )

        assert "距离不足" in result.update["messages"][0].content

    def test_end_combat_keeps_fallen_ally_revivable(self):
        """战斗结束时倒地友方回到场景单位池，不进入可清理尸体档案。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.combat_tools import end_combat
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        ally["hp"] = 0
        ally["death_save_failures"] = 1
        goblin = _make_goblin()
        goblin["hp"] = 0
        state = {
            "combat": _make_combat_state({"fighter_companion": ally, "goblin_1": goblin}, current_actor_id="fighter_companion"),
            "scene_units": {"fighter_companion": ally, "goblin_1": goblin},
            "space": _make_space_state(["fighter_companion", "goblin_1"]),
        }

        result = _invoke_tool(end_combat, tool_input={"state": state})

        assert "fighter_companion" in result.update["scene_units"]
        assert result.update["scene_units"]["fighter_companion"]["hp"] == 0
        assert "fighter_companion" not in result.update["dead_units"]
        assert "goblin_1" in result.update["dead_units"]


# ── Phase 3: attack_action 校验与动作消耗 ────────────────────────


class TestAttackActionValidation:
    """验证 attack_action 的前置校验（回合归属/目标存活/动作资源）"""

    def _invoke_attack(self, state: dict, attacker_id: str, target_id: str, **kwargs):
        from app.services.tools.combat_tools import attack_action
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
        if current_actor_id == "player_预设-战士":
            current_actor_id = player_c["id"]

        goblin = _make_goblin()
        combat = _make_combat_state(
            {player_c["id"]: player_c, "goblin_1": goblin},
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

    def test_extra_action_allows_attack_after_normal_action_is_spent(self):
        """动作如潮提供的额外动作应允许普通动作已用尽的角色继续攻击一次。"""
        state = self._build_state(current_actor_id="player_预设-战士", player_action_available=False)
        state["player"]["extra_action_available"] = True

        result = self._invoke_attack(state, "player_预设-战士", "goblin_1")

        assert isinstance(result, Command)
        assert result.update["player"]["action_available"] is False
        assert result.update["player"]["extra_action_available"] is False
        assert "攻击" in result.update["messages"][0].content

    def test_successful_attack_consumes_action(self):
        """攻击成功后 attacker.action_available 应为 False（使用怪物攻击以避免玩家 interrupt）"""
        state = self._build_state(current_actor_id="goblin_1")
        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert isinstance(result, Command)
        attacker = result.update["combat"]["participants"]["goblin_1"]
        assert attacker["action_available"] is False
        assert result.update["combat"]["current_actor_id"] == "goblin_1"

    def test_attack_deals_damage_from_weapon(self):
        """攻击使用武器数据而非硬编码（使用怪物攻击以避免玩家 interrupt）"""
        state = self._build_state(current_actor_id="goblin_1")
        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert isinstance(result, Command)
        msg_content = result.update["messages"][0].content
        assert "攻击" in msg_content

    def test_trip_attack_maneuver_adds_damage_and_prone_on_failed_save(self):
        """攻击流程中的摔绊攻击应消耗卓越骰、追加伤害，并在 STR 豁免失败时倒地。"""
        state = self._build_state(current_actor_id="player_预设-战士")
        player = state["player"]
        player.update({
            "level": 3,
            "fighter_archetype": "battle_master",
            "class_features": [*player.get("class_features", []), "combat_superiority", "student_of_war"],
            "maneuvers": ["trip_attack"],
            "superiority_die": "1d8",
            "maneuver_save_dc": 13,
        })
        player.setdefault("resources", {})["superiority_dice"] = 4
        combat_dict = state["combat"].model_dump()
        combat_dict["participants"]["goblin_1"]["hp"] = 20
        combat_dict["participants"]["goblin_1"]["max_hp"] = 20
        state["combat"] = CombatState(**combat_dict)
        real_roll = d20.roll

        def fixed_roll(expr: str):
            mapping = {
                "1d20+5": "15",
                "1d8+3": "5",
                "1d8": "4",
                "1d20-1": "7",
            }
            return real_roll(mapping.get(expr.replace(" ", ""), expr))

        with patch("app.services.tools._helpers.d20.roll", side_effect=fixed_roll):
            result = self._invoke_attack(
                state,
                "player_预设-战士",
                "goblin_1",
                maneuver_id="trip_attack",
            )

        updated_player = result.update["player"]
        updated_target = result.update["combat"]["participants"]["goblin_1"]
        content = result.update["messages"][0].content
        assert updated_player["resources"]["superiority_dice"] == 3
        assert updated_target["hp"] == 11
        assert any(condition["id"] == "prone" for condition in updated_target["conditions"])
        assert "摔绊攻击" in content
        assert "失败，陷入倒地" in content

    def test_trip_attack_maneuver_does_not_prone_on_successful_save(self):
        """目标 STR 豁免成功时，摔绊攻击只追加伤害不倒地。"""
        state = self._build_state(current_actor_id="player_预设-战士")
        player = state["player"]
        player.update({
            "level": 3,
            "fighter_archetype": "battle_master",
            "class_features": [*player.get("class_features", []), "combat_superiority", "student_of_war"],
            "maneuvers": ["trip_attack"],
            "superiority_die": "1d8",
            "maneuver_save_dc": 13,
        })
        player.setdefault("resources", {})["superiority_dice"] = 4
        combat_dict = state["combat"].model_dump()
        combat_dict["participants"]["goblin_1"]["hp"] = 20
        combat_dict["participants"]["goblin_1"]["max_hp"] = 20
        state["combat"] = CombatState(**combat_dict)
        real_roll = d20.roll

        def fixed_roll(expr: str):
            mapping = {
                "1d20+5": "15",
                "1d8+3": "5",
                "1d8": "4",
                "1d20-1": "18",
            }
            return real_roll(mapping.get(expr.replace(" ", ""), expr))

        with patch("app.services.tools._helpers.d20.roll", side_effect=fixed_roll):
            result = self._invoke_attack(
                state,
                "player_预设-战士",
                "goblin_1",
                maneuver_id="trip_attack",
            )

        updated_target = result.update["combat"]["participants"]["goblin_1"]
        content = result.update["messages"][0].content
        assert updated_target["hp"] == 11
        assert not any(condition["id"] == "prone" for condition in updated_target["conditions"])
        assert "成功，未倒地" in content

    def test_trip_attack_maneuver_does_not_consume_on_miss(self):
        """攻击未命中时，声明的摔绊攻击不应消耗卓越骰。"""
        state = self._build_state(current_actor_id="player_预设-战士")
        player = state["player"]
        player.update({
            "level": 3,
            "fighter_archetype": "battle_master",
            "class_features": [*player.get("class_features", []), "combat_superiority", "student_of_war"],
            "maneuvers": ["trip_attack"],
            "superiority_die": "1d8",
            "maneuver_save_dc": 13,
        })
        player.setdefault("resources", {})["superiority_dice"] = 4
        real_roll = d20.roll

        def fixed_roll(expr: str):
            return real_roll("2") if expr.replace(" ", "") == "1d20+5" else real_roll(expr)

        with patch("app.services.tools._helpers.d20.roll", side_effect=fixed_roll):
            result = self._invoke_attack(
                state,
                "player_预设-战士",
                "goblin_1",
                maneuver_id="trip_attack",
            )

        assert result.update["player"]["resources"]["superiority_dice"] == 4
        assert "未命中" in result.update["messages"][0].content

    def test_menacing_attack_adds_damage_and_frightened_on_failed_save(self):
        """恐吓攻击应追加卓越骰伤害，并在 WIS 豁免失败时施加恐慌。"""
        state = self._build_state(current_actor_id="player_预设-战士")
        player = state["player"]
        player.update({
            "level": 3,
            "fighter_archetype": "battle_master",
            "class_features": [*player.get("class_features", []), "combat_superiority", "student_of_war"],
            "maneuvers": ["menacing_attack"],
            "superiority_die": "1d8",
            "maneuver_save_dc": 13,
        })
        player.setdefault("resources", {})["superiority_dice"] = 4
        combat_dict = state["combat"].model_dump()
        combat_dict["participants"]["goblin_1"]["hp"] = 20
        combat_dict["participants"]["goblin_1"]["max_hp"] = 20
        state["combat"] = CombatState(**combat_dict)
        real_roll = d20.roll

        def fixed_roll(expr: str):
            mapping = {
                "1d20+5": "15",
                "1d8+3": "5",
                "1d8": "4",
                "1d20-1": "7",
            }
            return real_roll(mapping.get(expr.replace(" ", ""), expr))

        with patch("app.services.tools._helpers.d20.roll", side_effect=fixed_roll):
            result = self._invoke_attack(
                state,
                "player_预设-战士",
                "goblin_1",
                maneuver_id="menacing_attack",
            )

        updated_player = result.update["player"]
        updated_target = result.update["combat"]["participants"]["goblin_1"]
        content = result.update["messages"][0].content
        assert updated_player["resources"]["superiority_dice"] == 3
        assert updated_target["hp"] == 11
        assert any(condition["id"] == "frightened" for condition in updated_target["conditions"])
        assert "恐吓攻击" in content
        assert "失败，陷入恐慌" in content

    def test_pushing_attack_records_pending_forced_movement_on_failed_save(self):
        """推撞攻击第一版记录待推动结果，不直接猜测空间落点。"""
        state = self._build_state(current_actor_id="player_预设-战士")
        player = state["player"]
        player.update({
            "level": 3,
            "fighter_archetype": "battle_master",
            "class_features": [*player.get("class_features", []), "combat_superiority", "student_of_war"],
            "maneuvers": ["pushing_attack"],
            "superiority_die": "1d8",
            "maneuver_save_dc": 13,
        })
        player.setdefault("resources", {})["superiority_dice"] = 4
        combat_dict = state["combat"].model_dump()
        combat_dict["participants"]["goblin_1"]["hp"] = 20
        combat_dict["participants"]["goblin_1"]["max_hp"] = 20
        state["combat"] = CombatState(**combat_dict)
        real_roll = d20.roll

        def fixed_roll(expr: str):
            mapping = {
                "1d20+5": "15",
                "1d8+3": "5",
                "1d8": "4",
                "1d20-1": "7",
            }
            return real_roll(mapping.get(expr.replace(" ", ""), expr))

        with patch("app.services.tools._helpers.d20.roll", side_effect=fixed_roll):
            result = self._invoke_attack(
                state,
                "player_预设-战士",
                "goblin_1",
                maneuver_id="pushing_attack",
            )

        updated_target = result.update["combat"]["participants"]["goblin_1"]
        assert updated_target["hp"] == 11
        assert updated_target["forced_movement_pending"] == {
            "type": "push",
            "source_id": state["player"]["id"],
            "distance_feet": 15,
            "direction": "away_from_source",
        }
        assert "推撞攻击" in result.update["messages"][0].content

    def test_monster_attack_returns_pending_reaction_snapshot_when_reaction_available(self):
        """怪物攻击命中玩家且存在可用反应时，只写入 pending_reaction，不提前结算伤害。"""
        state = self._build_state(current_actor_id="goblin_1")
        roll_info = {
            "blocked": False,
            "emit_dice_roll": True,
            "hit": True,
            "crit": False,
            "natural": 12,
            "raw_roll": 12,
            "attack_bonus": 4,
            "hit_total": 16,
            "target_ac": 12,
            "dmg_dice": "1d6+2",
            "dmg_type": "slashing",
            "atk_name_display": "Scimitar",
            "advantage_used": "normal",
            "deflected": False,
            "lines": ["Goblin 使用 [Scimitar] 攻击 预设-战士!", "命中骰: 1d20+4 vs AC 12"],
        }

        with patch("app.services.tools.combat_tools.roll_attack_hit", return_value=roll_info), patch(
            "app.services.tools.combat_tools.get_available_reactions",
            return_value=[{"spell_id": "shield", "name_cn": "护盾术", "min_slot": 1}],
        ):
            result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert isinstance(result, Command)
        assert result.update["pending_reaction"]["attacker_id"] == "goblin_1"
        assert result.update["pending_reaction"]["target_id"] == "战士"
        assert result.update["pending_reaction"]["attack_roll"]["hit_total"] == 16
        assert result.update["reaction_choice"] is None
        assert result.update["messages"][0].additional_kwargs["hidden_from_ui"] is True
        assert "hp_changes" not in result.update
        assert result.update["player"]["hp"] == state["player"]["hp"]

    def test_melee_attack_rejected_when_space_distance_exceeds_reach(self):
        """空间系统启用后，近战攻击必须满足触及距离。"""
        state = self._build_state(current_actor_id="goblin_1")
        state["space"] = {
            "active_map_id": "map_1",
            "maps": {"map_1": {"id": "map_1", "name": "训练场", "width": 100, "height": 100}},
            "placements": {
                "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                "player_预设-战士": {"unit_id": "player_预设-战士", "map_id": "map_1", "position": {"x": 10, "y": 0}},
            },
        }

        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        msg = result.update["messages"][0].content
        assert "距离不足" in msg
        assert state["combat"].participants["goblin_1"].action_available is True

    def test_ranged_attack_allowed_within_normal_range(self):
        """远程武器使用自身射程，而不是近战 5 尺触及。"""
        player_c = _make_player_combatant("游侠")
        goblin = _make_goblin()
        combat = _make_combat_state(
            {player_c["id"]: player_c, "goblin_1": goblin},
            current_actor_id=player_c["id"],
            player_dict=player_c,
        )
        state = {
            "combat": combat,
            "player": player_c,
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "林道", "width": 200, "height": 100}},
                "placements": {
                    player_c["id"]: {"unit_id": player_c["id"], "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 60, "y": 0}},
                },
            },
        }

        result = self._invoke_attack(state, player_c["id"], "goblin_1", attack_name="Longbow")

        assert "射程不足" not in result.update["messages"][0].content
        assert result.update["player"]["action_available"] is False

    def test_ally_light_crossbow_allowed_within_normal_range(self):
        """友方轻弩攻击应按 80 尺普通射程校验，不能退化成 5 尺触及。"""
        from app.allies.profiles import get_ally_profile

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        goblin = _make_goblin()
        combat = _make_combat_state(
            {"fighter_companion": ally, "goblin_1": goblin},
            current_actor_id="fighter_companion",
        )
        state = {
            "combat": combat,
            "scene_units": {"fighter_companion": ally, "goblin_1": goblin},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "林道", "width": 200, "height": 100}},
                "placements": {
                    "fighter_companion": {"unit_id": "fighter_companion", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 60, "y": 0}},
                },
            },
        }

        result = self._invoke_attack(state, "fighter_companion", "goblin_1", attack_name="Light Crossbow")

        assert "距离不足" not in result.update["messages"][0].content
        assert result.update["combat"]["participants"]["fighter_companion"]["action_available"] is False

    def test_attack_rejected_when_space_exists_but_target_not_placed(self):
        """有地图时必须让参战单位拥有明确落点，避免距离规则被绕过。"""
        state = self._build_state(current_actor_id="goblin_1")
        state["space"] = {
            "active_map_id": "map_1",
            "maps": {"map_1": {"id": "map_1", "name": "训练场", "width": 100, "height": 100}},
            "placements": {
                "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
            },
        }

        result = self._invoke_attack(state, "goblin_1", "player_预设-战士")

        assert "尚未放置" in result.update["messages"][0].content


# ── Phase 4: next_turn 重置动作资源 ──────────────────────────────


class TestNextTurnReset:
    """验证 next_turn 正确重置下一个行动者的动作资源"""

    def test_next_actor_action_reset(self):
        from app.services.tools.combat_tools import next_turn

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
        from app.services.tools.combat_tools import end_combat

        goblin = _make_goblin()
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {"combat": combat}

        result = _invoke_tool(end_combat, tool_input={"state": state})
        assert result.update.get("phase") == "exploration"
        assert result.update.get("combat") is None

    def test_end_combat_keeps_full_battle_history_without_archive_metadata(self):
        from app.services.tools.combat_tools import end_combat

        goblin = _make_goblin()
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {
            "combat": combat,
            "messages": [
                HumanMessage(content="我冲向哥布林。"),
                ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_0"),
                ToolMessage(content="Goblin 倒下。", tool_call_id="call_1"),
            ],
            "active_combat_message_start": 1,
        }

        result = _invoke_tool(end_combat, tool_input={"state": state})

        assert result.update.get("active_combat_message_start") is None
        assert "combat_archives" not in result.update
        assert "共进行了" in result.update["messages"][0].content

    def test_end_combat_removes_dead_units_from_space(self):
        from app.services.tools.combat_tools import end_combat

        goblin = _make_goblin()
        goblin["hp"] = 0
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {
            "combat": combat,
            "space": _make_space_state(["goblin_1"]),
            "scene_units": {"goblin_1": goblin},
        }

        result = _invoke_tool(end_combat, tool_input={"state": state})

        assert "goblin_1" not in result.update["space"]["placements"]
        assert "goblin_1" in result.update["dead_units"]

    def test_end_combat_archives_departed_units_without_leaving_map_ghosts(self):
        from app.services.tools.combat_tools import end_combat

        goblin = _make_goblin()
        goblin["hp"] = 7
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {
            "combat": combat,
            "space": _make_space_state(["goblin_1"]),
            "scene_units": {"goblin_1": goblin},
        }

        result = _invoke_tool(end_combat, tool_input={"departed_unit_ids": ["goblin_1"], "state": state})

        assert "goblin_1" not in result.update["space"]["placements"]
        assert "goblin_1" not in result.update["scene_units"]
        assert "goblin_1" not in result.update["dead_units"]
        assert result.update["departed_units"]["goblin_1"]["hp"] == 7
        assert result.update["departed_units"]["goblin_1"]["departure_reason"] == "departed"
        assert "离场: Goblin" in result.update["messages"][0].content

    def test_end_combat_records_current_adventure_encounter_event(self):
        from app.services.tools.combat_tools import end_combat

        goblin = _make_goblin()
        goblin["hp"] = 0
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1")
        state = {
            "combat": combat,
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["lost_mine_start", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        }

        result = _invoke_tool(end_combat, tool_input={"state": state})

        assert "goblin_ambush_resolved" in result.update["adventure"]["completed_event_ids"]
        assert "节点收束与后续书签推进由后台导航器处理" in result.update["messages"][0].content

    def test_end_combat_awards_xp_for_fallen_enemies(self):
        from app.services.tools.combat_tools import end_combat

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良", "xp": 0}
        goblin = _make_goblin()
        goblin["hp"] = 0
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1", player_dict=player)
        state = {"combat": combat, "player": player, "scene_units": {"goblin_1": goblin}}

        result = _invoke_tool(end_combat, tool_input={"state": state})

        assert result.update["player"]["xp"] == 50
        assert result.update["player"]["claimed_combat_xp_ids"] == ["combat:goblin_1"]
        assert result.update["player"]["combat_xp_log"][0]["total_xp"] == 50
        assert "战斗 XP: +50" in result.update["messages"][0].content

    def test_end_combat_awards_xp_for_captured_enemy_when_declared_defeated(self):
        from app.services.tools.combat_tools import end_combat

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良", "xp": 10}
        goblin = _make_goblin()
        goblin["hp"] = 3
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1", player_dict=player)
        state = {"combat": combat, "player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["goblin_1"])}

        result = _invoke_tool(
            end_combat,
            tool_input={"departed_unit_ids": ["goblin_1"], "defeated_unit_ids": ["goblin_1"], "state": state},
        )

        assert result.update["player"]["xp"] == 60
        assert result.update["departed_units"]["goblin_1"]["departure_reason"] == "defeated"
        assert "goblin_1" not in result.update["dead_units"]
        assert "战斗 XP: +50" in result.update["messages"][0].content

    def test_end_combat_does_not_award_xp_for_enemy_that_only_escaped(self):
        from app.services.tools.combat_tools import end_combat

        player = {**PREDEFINED_CHARACTERS["战士"], "name": "温良", "id": "温良", "xp": 10}
        goblin = _make_goblin()
        goblin["hp"] = 3
        combat = _make_combat_state({"goblin_1": goblin}, current_actor_id="goblin_1", player_dict=player)
        state = {"combat": combat, "player": player, "scene_units": {"goblin_1": goblin}, "space": _make_space_state(["goblin_1"])}

        result = _invoke_tool(end_combat, tool_input={"departed_unit_ids": ["goblin_1"], "state": state})

        assert result.update["player"]["xp"] == 10
        assert "claimed_combat_xp_ids" not in result.update["player"]
        assert "战斗 XP" not in result.update["messages"][0].content


# ── Phase 6: WeaponData 模型验证 ────────────────────────────────


class TestWeaponDataModel:

    def test_weapon_data_fields(self):
        w = WeaponData(name="Longsword", damage_dice="1d8", damage_type="slashing",
                       weapon_type="melee", properties=["versatile"], versatile_damage_dice="1d10")
        assert w.name == "Longsword"
        assert w.weapon_type == "melee"
        assert "versatile" in w.properties
        assert w.versatile_damage_dice == "1d10"

    def test_weapon_data_defaults(self):
        w = WeaponData(name="Fist")
        assert w.damage_dice == "1d4"
        assert w.weapon_type == "melee"
        assert w.properties == []

    def test_lost_mine_weapon_registry_covers_module_weapons(self):
        from app.equipment.weapons import get_weapon_spec

        for weapon_name in [
            "Club",
            "Greatclub",
            "Javelin",
            "Mace",
            "Quarterstaff",
            "Shortbow",
            "Battleaxe",
            "Greataxe",
            "Longsword",
            "Morningstar",
            "Scimitar",
            "Shortsword",
            "Light Crossbow",
            "Heavy Crossbow",
            "Longbow",
            "Talon",
            "Hew",
            "Lightbringer",
            "Spider Staff",
        ]:
            assert get_weapon_spec(weapon_name) is not None, f"{weapon_name} 缺少武器规则"


# ── Phase 7: modify_character_state 资源同步 ─────────────────────


class TestModifyCharacterStateResources:
    """验证战斗中恢复资源时，玩家数据保持一致（新模型中玩家就是单一数据源）"""

    def test_player_spell_slot_recovery_uses_actual_current_value_in_combat(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
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
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
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

    def test_unknown_state_change_keys_fail_fast_without_update(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["hp"] = 4
        player["resources"] = {"spell_slot_lv1": 0}
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "changes": {"hp": 8, "resources": {"spell_slot_lv1": 2}},
                "reason": "测试错误字段",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert "player" not in result.update
        content = result.update["messages"][0].content
        assert "未知 changes 字段 hp, resources" in content
        assert "set_hp" in content
        assert "set_resource" in content

    def test_restore_hp_and_spell_slots_uses_explicit_state_change_keys(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["hp"] = 4
        player["resources"] = {"spell_slot_lv1": 0}
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "changes": {"set_hp": 8, "set_resource": {"spell_slot_lv1": "max"}},
                "reason": "测试恢复",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["hp"] == 8
        assert result.update["player"]["resources"]["spell_slot_lv1"] == 2

    def test_record_death_save_uses_unified_state_tool(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["id"] = "温良"
        player["name"] = "温良"
        player["side"] = "player"
        player["hp"] = 0
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "action": "record_death_save",
                "payload": {"roll_total": 13},
                "reason": "死亡豁免",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["death_save_successes"] == 1
        assert result.update["player"]["death_save_failures"] == 0
        assert "死亡豁免 d20=13：成功" in result.update["messages"][0].content

    def test_death_save_natural_20_revives_player(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["id"] = "温良"
        player["name"] = "温良"
        player["side"] = "player"
        player["hp"] = 0
        player["death_save_failures"] = 2
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "action": "record_death_save",
                "payload": {"roll_total": 20},
                "reason": "死亡豁免",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["hp"] == 1
        assert result.update["player"]["death_save_failures"] == 0
        assert result.update["hp_changes"][0]["new_hp"] == 1

    def test_revive_action_restores_hp_and_clears_death_saves(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["id"] = "温良"
        player["name"] = "温良"
        player["side"] = "player"
        player["hp"] = 0
        player["death_save_successes"] = 1
        player["death_save_failures"] = 2
        player["is_dead"] = True
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "action": "revive",
                "payload": {"hp": 4},
                "reason": "治疗药水",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["hp"] == 4
        assert result.update["player"]["death_save_successes"] == 0
        assert result.update["player"]["death_save_failures"] == 0
        assert result.update["player"]["is_dead"] is False

    def test_damage_at_zero_hp_adds_death_save_failure(self):
        from app.services.tools._helpers import apply_damage_to_target

        player = _make_player_combatant("法师")
        player["hp"] = 0
        player["death_save_failures"] = 1

        _, hp_change, lines = apply_damage_to_target(player, 3, damage_type="slashing")

        assert hp_change["old_hp"] == 0
        assert hp_change["new_hp"] == 0
        assert player["death_save_failures"] == 2
        assert any("死亡豁免失败 +1" in line for line in lines)

    def test_stable_player_does_not_record_more_death_saves(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["id"] = "温良"
        player["name"] = "温良"
        player["side"] = "player"
        player["hp"] = 0
        player["death_save_successes"] = 3
        player["death_save_failures"] = 0
        player["is_stable"] = True
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "action": "record_death_save",
                "payload": {"roll_total": 4},
                "reason": "死亡豁免",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["is_stable"] is True
        assert result.update["player"]["death_save_failures"] == 0
        assert "已经伤势稳定" in result.update["messages"][0].content

    def test_hp_delta_to_zero_marks_player_dying(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["id"] = "温良"
        player["name"] = "温良"
        player["side"] = "player"
        player["hp"] = 2
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "player",
                "action": "update",
                "changes": {"hp_delta": -5},
                "reason": "陷阱伤害",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["hp"] == 0
        assert result.update["player"]["death_save_successes"] == 0
        assert result.update["player"]["death_save_failures"] == 0
        assert result.update["player"]["is_dead"] is False
        assert "进入濒死" in result.update["messages"][0].content

    def test_next_turn_keeps_downed_unstable_player_in_order(self):
        from app.services.tools._helpers import advance_turn

        player = _make_player_combatant("法师")
        player["hp"] = 0
        player["death_save_successes"] = 0
        player["death_save_failures"] = 1
        goblin = _make_goblin()
        combat = _make_combat_state(
            {goblin["id"]: goblin, player["id"]: player},
            current_actor_id=goblin["id"],
            player_dict=player,
        ).model_dump()

        result_text = advance_turn(combat, player)

        assert combat["current_actor_id"] == player["id"]
        assert "当前行动者" in result_text
        assert player["action_available"] is False

    def test_grant_xp_action_uses_unified_state_tool(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "grant_xp",
                "payload": {"amount": 300},
                "reason": "击败地精巡逻队",
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert result.update["player"]["xp"] == player.get("xp", 0) + 300
        assert 'action="level_up"' in result.update["messages"][0].content

    def test_grant_xp_action_is_blocked_when_adventure_runtime_is_active(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        state = {
            "player": player,
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "cragmaw_hideout_entrance",
            },
        }

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "grant_xp",
                "payload": {"amount": 75, "reason": "打败伏击地精并发现克拉摩窝点"},
                "state": state,
            },
        )

        assert isinstance(result, Command)
        assert "player" not in result.update
        assert "本次未修改 XP" in result.update["messages"][0].content

    def test_grant_xp_action_can_target_scene_ally(self):
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        state = {"scene_units": {ally["id"]: ally}}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": ally["id"],
                "action": "grant_xp",
                "payload": {"amount": 300},
                "reason": "ally reward",
                "state": state,
            },
        )

        updated = result.update["scene_units"][ally["id"]]
        assert updated["xp"] == 300
        assert "player" not in result.update
        assert 'action="level_up"' in result.update["messages"][0].content

    def test_grant_xp_action_accepts_json_string_payload_for_scene_ally(self):
        """真实模型偶尔会把 payload 对象序列化成字符串，工具边界应兼容这种调用形态。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        state = {"scene_units": {ally["id"]: ally}}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": ally["id"],
                "action": "grant_xp",
                "payload": '{"amount": 900, "reason": "同伴奖励"}',
                "state": state,
            },
        )

        updated = result.update["scene_units"][ally["id"]]
        assert updated["xp"] == 900
        assert "player" not in result.update

    def test_update_action_accepts_json_string_changes(self):
        """changes 也在同一工具边界归一化，避免 update 动作因字符串对象失败。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "update",
                "changes": '{"hp_delta": -3}',
                "reason": "陷阱伤害",
                "state": state,
            },
        )

        assert result.update["player"]["hp"] == player["hp"] - 3

    def test_grant_xp_action_can_target_combat_ally(self):
        """战斗中的友方获得经验时，应写回 combat.participants 而不是玩家状态。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        goblin = _make_goblin()
        state = {
            "combat": _make_combat_state({"fighter_companion": ally, "goblin_1": goblin}, current_actor_id="fighter_companion"),
        }

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "fighter_companion",
                "action": "grant_xp",
                "payload": {"amount": 300},
                "reason": "ally reward",
                "state": state,
            },
        )

        updated = result.update["combat"]["participants"]["fighter_companion"]
        assert updated["xp"] == 300
        assert result.update["combat"]["participants"]["goblin_1"]["hp"] == goblin["hp"]
        assert "player" not in result.update

    def test_grant_xp_action_rejects_non_character_enemy(self):
        """普通怪物没有职业成长字段，不能通过经验工具升级。"""
        from app.services.tools.character_tools import modify_character_state

        goblin = _make_goblin()
        state = {"scene_units": {"goblin_1": goblin}}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "goblin_1",
                "action": "grant_xp",
                "payload": {"amount": 300},
                "reason": "invalid reward",
                "state": state,
            },
        )

        assert "scene_units" not in result.update
        assert "不是可升级的玩家或友方角色" in result.update["messages"][0].content

    def test_level_up_and_arcane_tradition_actions_use_unified_state_tool(self):
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["xp"] = 300
        state = {"player": player}

        level_result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "level_up",
                "state": state,
            },
        )
        updated_player = level_result.update["player"]

        tradition_result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_arcane_tradition",
                "payload": {"tradition": "abjuration"},
                "state": {"player": updated_player},
            },
        )

        assert isinstance(level_result, Command)
        assert updated_player["level"] == 2
        assert isinstance(tradition_result, Command)
        assert tradition_result.update["player"]["arcane_tradition"] == "abjuration"
        assert "arcane_ward" in tradition_result.update["player"]["class_features"]
        assert any(c.get("id") == "arcane_ward" for c in tradition_result.update["player"]["conditions"])

    def test_level_up_action_can_target_scene_ally(self):
        """友方角色升级应按 target_id 写回 scene_units，不污染玩家状态。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state

        ally = get_ally_profile("fighter_companion")
        ally["xp"] = 300
        state = {"scene_units": {"fighter_companion": ally}}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "fighter_companion",
                "action": "level_up",
                "state": state,
            },
        )

        updated = result.update["scene_units"]["fighter_companion"]
        assert updated["level"] == 2
        assert "action_surge" in updated["class_features"]
        assert updated["action_available"] is True
        assert updated["attacks"]
        assert "player" not in result.update

    def test_level_up_refreshes_combat_fields_without_resetting_action_economy(self):
        """战斗中成长会刷新攻击等派生字段，但不能返还本回合已消耗的动作资源。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        ally["xp"] = 300
        ally["action_available"] = False
        ally["bonus_action_available"] = False
        ally["reaction_available"] = False
        ally["movement_left"] = 5
        state = {
            "combat": _make_combat_state({"fighter_companion": ally}, current_actor_id="fighter_companion"),
        }

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "fighter_companion",
                "action": "level_up",
                "state": state,
            },
        )

        updated = result.update["combat"]["participants"]["fighter_companion"]
        assert updated["level"] == 2
        assert updated["attacks"]
        assert updated["action_available"] is False
        assert updated["bonus_action_available"] is False
        assert updated["reaction_available"] is False
        assert updated["movement_left"] == 5

    def test_choose_fighter_archetype_can_target_combat_ally(self):
        """战斗中的战士友方达到 3 级后，可以选择武术范型并写回 combat。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("sildar"), side="ally")
        state = {
            "combat": _make_combat_state({"sildar": ally}, current_actor_id="sildar"),
        }

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "sildar",
                "action": "choose_fighter_archetype",
                "payload": {"archetype": "battle_master"},
                "state": state,
            },
        )

        updated = result.update["combat"]["participants"]["sildar"]
        assert updated["fighter_archetype"] == "battle_master"
        assert updated["resources"]["superiority_dice"] == 4
        assert updated["maneuvers_known_count"] == 3
        assert updated["attacks"]
        assert "player" not in result.update

    def test_choose_arcane_tradition_can_target_scene_ally(self):
        """法师友方可以通过 target_id 选择奥术传统并写回 scene_units。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state

        ally = get_ally_profile("apprentice_wizard")
        state = {"scene_units": {"apprentice_wizard": ally}}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "apprentice_wizard",
                "action": "choose_arcane_tradition",
                "payload": {"tradition": "evocation"},
                "state": state,
            },
        )

        updated = result.update["scene_units"]["apprentice_wizard"]
        assert updated["arcane_tradition"] == "evocation"
        assert "sculpt_spells" in updated["class_features"]
        assert "player" not in result.update

    def test_choose_feat_action_records_tough_on_player(self):
        """专长第一版通过注册表写入字段，并复用成长回写刷新派生状态。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["level"] = 4
        old_max_hp = player["max_hp"]
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_feat",
                "payload": {"feat": "tough"},
                "state": state,
            },
        )

        updated = result.update["player"]
        assert "tough" in updated["feats"]
        assert "feat_tough" in updated["class_features"]
        assert updated["feat_tough_hp_bonus"] == 8
        assert updated["max_hp"] == old_max_hp + 8
        assert updated["attacks"]

    def test_choose_feat_action_can_target_combat_ally_without_resetting_action_economy(self):
        """战斗中友方可选择专长，但不能因此返还已消耗的动作经济。"""
        from app.allies.profiles import get_ally_profile
        from app.services.tools.character_tools import modify_character_state
        from app.services.tools._helpers import prepare_character_for_combat

        ally = prepare_character_for_combat(get_ally_profile("fighter_companion"), side="ally")
        ally["action_available"] = False
        ally["bonus_action_available"] = False
        ally["reaction_available"] = False
        ally["movement_left"] = 5
        state = {
            "combat": _make_combat_state({"fighter_companion": ally}, current_actor_id="fighter_companion"),
        }

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "target_id": "fighter_companion",
                "action": "choose_feat",
                "payload": {"feat": "sharpshooter"},
                "state": state,
            },
        )

        updated = result.update["combat"]["participants"]["fighter_companion"]
        assert "sharpshooter" in updated["feats"]
        assert "feat_sharpshooter" in updated["class_features"]
        assert updated["sharpshooter_enabled"] is False
        assert updated["action_available"] is False
        assert updated["bonus_action_available"] is False
        assert updated["reaction_available"] is False
        assert updated["movement_left"] == 5

    def test_choose_feat_action_rejects_duplicate_feat(self):
        """重复选择同一专长应直接拒绝，避免重复叠加字段效果。"""
        from app.services.tools.character_tools import modify_character_state

        player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
        player["feats"] = ["tough"]
        state = {"player": player}

        result = _invoke_tool(
            modify_character_state,
            tool_input={
                "action": "choose_feat",
                "payload": {"feat": "tough"},
                "state": state,
            },
        )

        assert "player" not in result.update
        assert "已选择专长" in result.update["messages"][0].content


# ── Phase 8: Mage Armor 法术回归测试 ───────────────────────────


class TestMageArmorSpell:
    """验证法师护甲已注册、已装载到预设施法者，并能通过状态系统生效"""

    def test_mage_armor_loaded_on_wizard_and_sorcerer(self):
        # 1 级法师/术士默认学习法师护甲，护盾术留给后续升级取得。
        assert "mage_armor" in PREDEFINED_CHARACTERS["法师"]["known_spells"]
        assert "mage_armor" in PREDEFINED_CHARACTERS["术士"]["known_spells"]
        assert "shield" not in PREDEFINED_CHARACTERS["法师"]["known_spells"]

    def test_mage_armor_condition_is_registered(self):
        from app.conditions import get_condition_def

        condition_def = get_condition_def("mage_armor")
        assert condition_def is not None
        assert condition_def.name_cn == "法师护甲"

    def test_cast_mage_armor_sets_ac_and_condition(self):
        from app.services.tools.spell_tools import cast_spell
        from app.services.tools._helpers import compute_ac

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["mage_armor"]
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
        assert updated_player["ac"] == 15
        assert updated_player["resources"]["spell_slot_lv1"] == 1
        assert any(c.get("id") == "mage_armor" for c in updated_player["conditions"])

    def test_extra_action_allows_action_spell_after_normal_action_is_spent(self):
        """动作如潮的额外动作应能支付施法时间为 action 的法术。"""
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        prepare_player_for_combat(player)
        player["action_available"] = False
        player["extra_action_available"] = True
        goblin = _make_goblin()
        combat = _make_combat_state(
            {player["id"]: player, "goblin_1": goblin},
            current_actor_id=player["id"],
            player_dict=player,
        )
        state = {"player": player, "combat": combat}

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fire_bolt",
                "target_ids": ["goblin_1"],
                "state": state,
            },
        )

        updated_player = result.update["player"]
        assert updated_player["action_available"] is False
        assert updated_player["extra_action_available"] is False
        assert "本回合的动作已用尽" not in result.update["messages"][0].content


class TestSpellRangeValidation:
    """验证法术在空间系统启用后遵守施法距离。"""

    def test_ranged_cantrip_rejected_when_target_is_out_of_range(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        goblin = _make_goblin()
        state = {
            "player": player,
            "scene_units": {"goblin_1": goblin},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "长廊", "width": 300, "height": 60}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 130, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fire_bolt",
                "target_ids": ["goblin_1"],
                "state": state,
            },
        )

        assert "距离不足" in result.update["messages"][0].content

    def test_touch_spell_uses_five_feet_range(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["牧师"])
        ally = _make_goblin("ally_1")
        ally["side"] = "ally"
        ally["hp"] = 1
        state = {
            "player": player,
            "scene_units": {"ally_1": ally},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "神殿", "width": 40, "height": 40}},
                "placements": {
                    "player_预设-牧师": {"unit_id": "player_预设-牧师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "ally_1": {"unit_id": "ally_1", "map_id": "map_1", "position": {"x": 10, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "cure_wounds",
                "target_ids": ["ally_1"],
                "slot_level": 1,
                "state": state,
            },
        )

        assert "距离不足" in result.update["messages"][0].content

    def test_cure_wounds_clears_death_saves_on_healed_target(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["牧师"])
        ally = _make_goblin("ally_1")
        ally["side"] = "ally"
        ally["hp"] = 0
        ally["death_save_successes"] = 1
        ally["death_save_failures"] = 2
        state = {
            "player": player,
            "scene_units": {"ally_1": ally},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "神殿", "width": 40, "height": 40}},
                "placements": {
                    "player_预设-牧师": {"unit_id": "player_预设-牧师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "ally_1": {"unit_id": "ally_1", "map_id": "map_1", "position": {"x": 1, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "cure_wounds",
                "target_ids": ["ally_1"],
                "slot_level": 1,
                "state": state,
            },
        )

        healed_ally = result.update["scene_units"]["ally_1"]
        assert healed_ally["hp"] > 0
        assert healed_ally["death_save_successes"] == 0
        assert healed_ally["death_save_failures"] == 0
        assert healed_ally["is_dead"] is False

    def test_self_spell_does_not_require_placement_distance(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["mirror_image"]
        player["resources"]["spell_slot_lv2"] = 1
        state = {
            "player": player,
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "镜厅", "width": 40, "height": 40}},
                "placements": {},
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "mirror_image",
                "target_ids": ["self"],
                "slot_level": 2,
                "state": state,
            },
        )

        assert "尚未放置" not in result.update["messages"][0].content
        assert any(c.get("id") == "mirror_image" for c in result.update["player"]["conditions"])

    def test_fireball_target_point_auto_selects_units_inside_area(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["level"] = 5
        player["known_spells"] = list(player["known_spells"]) + ["fireball"]
        player["resources"]["spell_slot_lv3"] = 1
        near = _make_goblin("near_goblin")
        edge = _make_goblin("edge_goblin")
        far = _make_goblin("far_goblin")
        state = {
            "player": player,
            "scene_units": {"near_goblin": near, "edge_goblin": edge, "far_goblin": far},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "广场", "width": 200, "height": 200}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "near_goblin": {"unit_id": "near_goblin", "map_id": "map_1", "position": {"x": 35, "y": 30}},
                    "edge_goblin": {"unit_id": "edge_goblin", "map_id": "map_1", "position": {"x": 50, "y": 30}},
                    "far_goblin": {"unit_id": "far_goblin", "map_id": "map_1", "position": {"x": 70, "y": 30}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fireball",
                "target_ids": [],
                "target_point": {"x": 30, "y": 30},
                "slot_level": 3,
                "state": state,
            },
        )

        assert result.update["scene_units"]["near_goblin"]["hp"] < near["hp"]
        assert result.update["scene_units"]["edge_goblin"]["hp"] < edge["hp"]
        assert result.update["scene_units"]["far_goblin"]["hp"] == far["hp"]
        assert result.update["player"]["resources"]["spell_slot_lv3"] == 0

    def test_fireball_area_can_include_caster(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["level"] = 5
        player["known_spells"] = list(player["known_spells"]) + ["fireball"]
        player["resources"]["spell_slot_lv3"] = 1
        state = {
            "player": player,
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "密室", "width": 100, "height": 100}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 10, "y": 10}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fireball",
                "target_ids": [],
                "target_point": {"x": 15, "y": 10},
                "slot_level": 3,
                "state": state,
            },
        )

        assert result.update["player"]["hp"] < player["hp"]

    def test_fireball_target_point_rejected_outside_cast_range(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["level"] = 5
        player["known_spells"] = list(player["known_spells"]) + ["fireball"]
        player["resources"]["spell_slot_lv3"] = 1
        state = {
            "player": player,
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "旷野", "width": 300, "height": 300}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fireball",
                "target_ids": [],
                "target_point": {"x": 180, "y": 0},
                "slot_level": 3,
                "state": state,
            },
        )

        assert "目标点距离 180.0 尺" in result.update["messages"][0].content

    def test_thunderwave_uses_facing_square_area(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["thunderwave"]
        front = _make_goblin("front_goblin")
        side = _make_goblin("side_goblin")
        state = {
            "player": player,
            "scene_units": {"front_goblin": front, "side_goblin": side},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "石室", "width": 80, "height": 80}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 10, "y": 10}, "facing_deg": 0},
                    "front_goblin": {"unit_id": "front_goblin", "map_id": "map_1", "position": {"x": 20, "y": 10}},
                    "side_goblin": {"unit_id": "side_goblin", "map_id": "map_1", "position": {"x": 10, "y": 20}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "thunderwave",
                "target_ids": [],
                "slot_level": 1,
                "state": state,
            },
        )

        assert result.update["scene_units"]["front_goblin"]["hp"] < front["hp"]
        assert result.update["scene_units"]["side_goblin"]["hp"] == side["hp"]

    def test_burning_hands_uses_facing_cone_area(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["术士"])
        center = _make_goblin("center_goblin")
        outside = _make_goblin("outside_goblin")
        state = {
            "player": player,
            "scene_units": {"center_goblin": center, "outside_goblin": outside},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "甬道", "width": 80, "height": 80}},
                "placements": {
                    "player_预设-术士": {"unit_id": "player_预设-术士", "map_id": "map_1", "position": {"x": 10, "y": 10}, "facing_deg": 0},
                    "center_goblin": {"unit_id": "center_goblin", "map_id": "map_1", "position": {"x": 20, "y": 10}},
                    "outside_goblin": {"unit_id": "outside_goblin", "map_id": "map_1", "position": {"x": 20, "y": 20}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "burning_hands",
                "target_ids": [],
                "slot_level": 1,
                "state": state,
            },
        )

        assert result.update["scene_units"]["center_goblin"]["hp"] < center["hp"]
        assert result.update["scene_units"]["outside_goblin"]["hp"] == outside["hp"]

    def test_ice_knife_expands_area_around_primary_target_only(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["吟游诗人"])
        player["known_spells"] = list(player["known_spells"]) + ["ice_knife"]
        primary = _make_goblin("primary_goblin")
        splash = _make_goblin("splash_goblin")
        far = _make_goblin("far_goblin")
        state = {
            "player": player,
            "scene_units": {"primary_goblin": primary, "splash_goblin": splash, "far_goblin": far},
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "码头", "width": 120, "height": 80}},
                "placements": {
                    "player_预设-吟游诗人": {"unit_id": "player_预设-吟游诗人", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                    "primary_goblin": {"unit_id": "primary_goblin", "map_id": "map_1", "position": {"x": 30, "y": 0}},
                    "splash_goblin": {"unit_id": "splash_goblin", "map_id": "map_1", "position": {"x": 34, "y": 0}},
                    "far_goblin": {"unit_id": "far_goblin", "map_id": "map_1", "position": {"x": 40, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "ice_knife",
                "target_ids": ["primary_goblin"],
                "slot_level": 1,
                "state": state,
            },
        )

        assert result.update["scene_units"]["primary_goblin"]["hp"] < primary["hp"]
        assert result.update["scene_units"]["splash_goblin"]["hp"] < splash["hp"]
        assert result.update["scene_units"]["far_goblin"]["hp"] == far["hp"]


class TestLostMineSpellCoverage:
    """验证《失落矿坑》施法怪需要的战斗法术已进入本地注册表。"""

    def test_priority_spells_registered(self):
        from app.spells import get_spell_def

        for spell_id in [
            "shocking_grasp",
            "charm_person",
            "misty_step",
            "blur",
            "flaming_sphere",
            "darkness",
            "faerie_fire",
            "invisibility",
            "suggestion",
        ]:
            assert get_spell_def(spell_id) is not None

    def test_end_concentration_clears_conditions_on_targets(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["hold_person"]
        player["resources"]["spell_slot_lv2"] = 1
        goblin = _make_goblin()
        goblin["conditions"] = [create_condition("paralyzed", source_id="concentration:hold_person", duration=10)]
        player["concentrating_on"] = "hold_person"
        state = {"player": player, "scene_units": {"goblin_1": goblin}}

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "hold_person",
                "target_ids": [],
                "end_concentration": True,
                "state": state,
            },
        )

        assert result.update["player"]["concentrating_on"] is None
        assert result.update["scene_units"]["goblin_1"]["conditions"] == []

    def test_misty_step_updates_space_without_using_movement(self):
        from app.services.tools.spell_tools import cast_spell

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["misty_step"]
        player["resources"]["spell_slot_lv2"] = 1
        state = {
            "player": player,
            "space": {
                "active_map_id": "map_1",
                "maps": {"map_1": {"id": "map_1", "name": "矿道", "width": 80, "height": 80}},
                "placements": {
                    "player_预设-法师": {"unit_id": "player_预设-法师", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                },
            },
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "misty_step",
                "target_ids": [],
                "target_point": {"x": 20, "y": 0},
                "slot_level": 2,
                "state": state,
            },
        )

        assert result.update["space"]["placements"]["法师"]["position"] == {"x": 20.0, "y": 0.0}
        assert result.update["player"]["resources"]["spell_slot_lv2"] == 0

    def test_blur_condition_gives_attackers_disadvantage(self):
        from app.conditions import get_combat_effects

        effects = get_combat_effects("blurred")
        assert effects.defend_advantage == "disadvantage"


class TestReactionFlow:
    """验证怪物命中后的显式待决反应链路与护盾术改判。"""

    _real_roll = d20.roll

    @staticmethod
    def _fixed_roll(expr: str):
        normalized = expr.replace(" ", "")
        mapping = {
            "1d20+4": "16",
            "1d6+2": "5",
        }
        return TestReactionFlow._real_roll(mapping.get(normalized, expr))

    def _build_reaction_state(self) -> dict:
        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["shield"]
        prepare_player_for_combat(player)
        goblin = _make_goblin()
        combat = _make_combat_state(
            {player["id"]: player, "goblin_1": goblin},
            current_actor_id="goblin_1",
            player_dict=player,
        )
        return {"combat": combat, "player": player}

    def test_monster_hit_creates_pending_reaction(self):
        from app.services.tools.combat_tools import attack_action

        state = self._build_reaction_state()
        with patch("app.services.tools._helpers.d20.roll", side_effect=self._fixed_roll), patch(
            "app.services.tools._helpers._get_natural_d20", return_value=12
        ):
            result = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": state["player"]["id"],
                    "state": state,
                },
            ).update

        pending = result["pending_reaction"]
        assert pending["type"] == "reaction_prompt"
        assert pending["attacker_id"] == "goblin_1"
        assert pending["attack_roll"]["raw_roll"] == 12
        assert pending["attack_roll"]["attack_bonus"] == 4
        assert pending["attack_roll"]["hit_total"] == 16
        assert pending["available_reactions"][0]["spell_id"] == "shield"
        assert result["messages"][0].additional_kwargs["hidden_from_ui"] is True

    def test_shield_reaction_uses_saved_attack_snapshot_and_prevents_damage(self):
        from app.graph import nodes
        from app.services.tools.combat_tools import attack_action

        state = self._build_reaction_state()
        with patch("app.services.tools._helpers.d20.roll", side_effect=self._fixed_roll), patch(
            "app.services.tools._helpers._get_natural_d20", return_value=12
        ):
            pending_state = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": state["player"]["id"],
                    "state": state,
                },
            ).update
            initial_slots = pending_state["player"]["resources"]["spell_slot_lv1"]
            resolved = nodes.resolve_reaction_node({
                "combat": pending_state["combat"],
                "player": pending_state["player"],
                "pending_reaction": pending_state["pending_reaction"],
                "reaction_choice": {"spell_id": "shield", "slot_level": 1},
            })

        assert resolved["pending_reaction"] is None
        assert resolved["reaction_choice"] is None
        assert resolved["hp_changes"] == []
        assert resolved["player"]["hp"] == PREDEFINED_CHARACTERS["法师"]["hp"]
        assert resolved["player"]["resources"]["spell_slot_lv1"] == initial_slots - 1
        assert resolved["combat"]["current_actor_id"] == "goblin_1"
        assert any(c.get("id") == "shield_active" for c in resolved["player"].get("conditions", []))

        message = resolved["messages"][0]
        assert "护盾术" in message.content
        assert "未命中" in message.content
        assert message.additional_kwargs["attack_roll"]["raw_roll"] == 12
        assert message.additional_kwargs["attack_roll"]["final_total"] == 16

    def test_shield_reaction_refreshes_arcane_ward(self):
        from app.graph import nodes
        from app.services.tools.combat_tools import attack_action

        state = self._build_reaction_state()
        state["player"]["level"] = 2
        state["player"]["class_features"] = [*state["player"].get("class_features", []), "arcane_ward"]
        state["player"]["conditions"].append(
            create_condition("arcane_ward", source_id=state["player"]["name"], extra=build_condition_extra(ward_hp=1, ward_max=7))
        )

        with patch("app.services.tools._helpers.d20.roll", side_effect=self._fixed_roll), patch(
            "app.services.tools._helpers._get_natural_d20", return_value=12
        ):
            pending_state = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": state["player"]["id"],
                    "state": state,
                },
            ).update
            resolved = nodes.resolve_reaction_node({
                "combat": pending_state["combat"],
                "player": pending_state["player"],
                "pending_reaction": pending_state["pending_reaction"],
                "reaction_choice": {"spell_id": "shield", "slot_level": 1},
            })

        ward = next(c for c in resolved["player"]["conditions"] if c.get("id") == "arcane_ward")
        assert ward["extra"]["ward_hp"] == 3
        assert "奥术结界" in resolved["messages"][0].content

    def test_player_spell_can_be_counterspelled_by_monster_reaction(self):
        from app.services.tools.spell_tools import cast_spell
        from app.monsters.models import MonsterAction

        state = self._build_reaction_state()
        player = state["player"]
        state["combat"].current_actor_id = player["id"]
        player["known_spells"] = list(player["known_spells"]) + ["fireball"]
        player["resources"]["spell_slot_lv3"] = 1
        mage = {
            "id": "mage_1",
            "name": "Evil Mage",
            "side": "enemy",
            "hp": 20,
            "max_hp": 20,
            "ac": 12,
            "base_ac": 12,
            "modifiers": {"int": 3},
            "reaction_available": True,
            "actions": [
                MonsterAction(
                    id="counterspell",
                    name="Counterspell",
                    kind="spell",
                    action_type="reaction",
                    spell_id="counterspell",
                    slot_level=3,
                ).model_dump()
            ],
        }
        state["combat"].participants = {
            "goblin_1": state["combat"].participants["goblin_1"],
            "mage_1": CombatantState(**mage),
        }

        result = _invoke_tool(
            cast_spell,
            tool_input={
                "spell_id": "fireball",
                "target_ids": ["goblin_1"],
                "slot_level": 3,
                "state": state,
            },
        ).update

        assert result["player"]["resources"]["spell_slot_lv3"] == 0
        assert result["combat"]["participants"]["mage_1"]["reaction_available"] is False
        assert "法术反制" in result["messages"][0].content
        assert "被法术反制打断" in result["messages"][0].content

    def test_counterspell_reaction_refreshes_arcane_ward(self):
        from app.services.tools.reactions import execute_player_reaction

        player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        player["known_spells"] = list(player["known_spells"]) + ["counterspell"]
        player["resources"]["spell_slot_lv3"] = 1
        player["class_features"] = [*player.get("class_features", []), "arcane_ward"]
        player["level"] = 5
        prepare_player_for_combat(player)

        result = execute_player_reaction(
            player,
            {"spell_id": "counterspell", "slot_level": 3},
            {
                "trigger_caster_name": "Evil Mage",
                "trigger_spell_name_cn": "火球术",
                "trigger_spell_level": 3,
                "targets": [{"id": "mage_1", "name": "Evil Mage"}],
            },
        )

        ward = next(c for c in player["conditions"] if c.get("id") == "arcane_ward")
        assert result.blocked_action is True
        assert ward["extra"]["ward_hp"] == 13
        assert "奥术结界" in "\n".join(result.lines)

    def test_skip_reaction_keeps_original_damage_roll(self):
        from app.graph import nodes
        from app.services.tools.combat_tools import attack_action

        state = self._build_reaction_state()
        with patch("app.services.tools._helpers.d20.roll", side_effect=self._fixed_roll), patch(
            "app.services.tools._helpers._get_natural_d20", return_value=12
        ):
            pending_state = _invoke_tool(
                attack_action,
                tool_input={
                    "attacker_id": "goblin_1",
                    "target_id": state["player"]["id"],
                    "state": state,
                },
            ).update
            resolved = nodes.resolve_reaction_node({
                "combat": pending_state["combat"],
                "player": pending_state["player"],
                "pending_reaction": pending_state["pending_reaction"],
                "reaction_choice": {"spell_id": None},
            })

        assert len(resolved["hp_changes"]) == 1
        hp_change = resolved["hp_changes"][0]
        assert hp_change["old_hp"] == PREDEFINED_CHARACTERS["法师"]["hp"]
        assert hp_change["new_hp"] == PREDEFINED_CHARACTERS["法师"]["hp"] - 5
        assert resolved["combat"]["current_actor_id"] == "goblin_1"
        assert resolved["messages"][0].additional_kwargs["attack_roll"]["final_total"] == 16


class TestConditionLifecycleHooks:
    """验证条件生命周期已从主流程特判收敛到统一 hook。"""

    _real_roll = d20.roll

    @staticmethod
    def _mirror_image_roll(expr: str):
        normalized = expr.replace(" ", "")
        mapping = {
            "1d20+4": "16",
            "1d20": "18",
        }
        return TestConditionLifecycleHooks._real_roll(mapping.get(normalized, expr))

    def test_paralyzed_monster_turn_does_not_attach_attack_roll_payload(self):
        from app.services.tools.combat_tools import attack_action

        player = _make_player_combatant("战士")
        goblin = _make_goblin()
        goblin["conditions"] = [create_condition("paralyzed", source_id="concentration:hold_person", duration=3)]
        combat = _make_combat_state(
            {player["id"]: player, "goblin_1": goblin},
            current_actor_id="goblin_1",
            player_dict=player,
        )

        result = _invoke_tool(
            attack_action,
            tool_input={
                "attacker_id": "goblin_1",
                "target_id": player["id"],
                "state": {"combat": combat, "player": player},
            },
        ).update

        message = result["messages"][0]
        assert "无法行动" in message.content
        assert not getattr(message, "additional_kwargs", {}).get("attack_roll")

    def test_mirror_image_deflection_runs_via_condition_hook(self):
        from app.services.tools._helpers import roll_attack_hit

        attacker = _make_goblin()
        target = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
        prepare_player_for_combat(target)
        target["conditions"] = [
            create_condition(
                "mirror_image",
                source_id=target["name"],
                duration=10,
                extra=build_condition_extra(images=3),
            )
        ]

        with patch("app.services.tools._helpers.d20.roll", side_effect=self._mirror_image_roll), patch(
            "app.services.tools._helpers._get_natural_d20", return_value=12
        ), patch("app.conditions.mirror_image.d20.roll", side_effect=self._mirror_image_roll):
            roll_info = roll_attack_hit(attacker, target)

        assert roll_info["deflected"] is True
        assert roll_info["hit"] is False
        assert roll_info["crit"] is False
        assert any("攻击转向镜像" in line for line in roll_info["lines"])
        assert target["conditions"][0]["extra"]["images"] == 2


class TestSecondRoundConditionCleanup:
    """验证第二轮收口后的 save、damage 与移动 condition 逻辑。"""

    _real_roll = d20.roll

    @staticmethod
    def _ray_of_frost_roll(expr: str):
        normalized = expr.replace(" ", "")
        mapping = {
            "1d20+5": "17",
            "1d8": "4",
        }
        return TestSecondRoundConditionCleanup._real_roll(mapping.get(normalized, expr))

    @staticmethod
    def _toll_the_dead_roll(expr: str):
        normalized = expr.replace(" ", "")
        mapping = {
            "1d20+0": "5",
            "1d8": "4",
        }
        return TestSecondRoundConditionCleanup._real_roll(mapping.get(normalized, expr))

    @staticmethod
    def _ice_knife_roll(expr: str):
        normalized = expr.replace(" ", "")
        mapping = {
            "1d20+5": "15",
            "1d10": "4",
            "2d6": "6",
        }
        return TestSecondRoundConditionCleanup._real_roll(mapping.get(normalized, expr))

    def test_ray_of_frost_applies_condition_and_expires_on_caster_turn_start(self):
        from app.conditions import has_condition
        from app.services.tools._helpers import advance_turn, compute_current_speed
        from app.spells import ray_of_frost

        caster = _make_player_combatant("法师")
        goblin = _make_goblin()
        combat = _make_combat_state(
            {caster["id"]: caster, "goblin_1": goblin},
            current_actor_id=caster["id"],
            player_dict=caster,
        ).model_dump()
        target = combat["participants"]["goblin_1"]

        with patch("app.spells._resolvers.d20.roll", side_effect=self._ray_of_frost_roll):
            ray_of_frost.execute(caster, [target], 0, cantrip_scale=1)

        assert has_condition(target.get("conditions", []), "ray_of_frost_slow")
        assert target["speed"] == 30
        assert compute_current_speed(target) == 20

        advance_turn(combat, caster)
        assert combat["current_actor_id"] == "goblin_1"
        assert has_condition(target.get("conditions", []), "ray_of_frost_slow")
        assert target["movement_left"] == 20

        advance_turn(combat, caster)
        assert combat["current_actor_id"] == caster["id"]
        assert not has_condition(target.get("conditions", []), "ray_of_frost_slow")

    def test_toll_the_dead_damage_respects_arcane_ward(self):
        from app.spells import toll_the_dead

        caster = _make_player_combatant("法师")
        target = {
            "id": "warded_target",
            "name": "Ward Target",
            "hp": 10,
            "max_hp": 10,
            "modifiers": {"wis": 0, "con": 0},
            "conditions": [
                create_condition(
                    "arcane_ward",
                    extra=build_condition_extra(ward_hp=5),
                )
            ],
        }

        with patch("app.services.tools._helpers.d20.roll", side_effect=self._toll_the_dead_roll), patch(
            "app.spells.toll_the_dead.d20.roll", side_effect=self._toll_the_dead_roll
        ):
            result = toll_the_dead.execute(caster, [target], 0, cantrip_scale=1)

        assert result["hp_changes"][0]["old_hp"] == 10
        assert result["hp_changes"][0]["new_hp"] == 10
        assert target["conditions"][0]["extra"]["ward_hp"] == 1
        assert any("奥术结界" in line for line in result["lines"])

    def test_ice_knife_dex_save_uses_condition_auto_fail(self):
        from app.spells import ice_knife

        caster = _make_player_combatant("法师")
        target = _make_goblin()
        target["hp"] = 20
        target["max_hp"] = 20
        target["conditions"] = [
            create_condition("paralyzed", source_id="concentration:hold_person", duration=3)
        ]

        with patch("app.spells.ice_knife.d20.roll", side_effect=self._ice_knife_roll):
            result = ice_knife.execute(caster, [target], 1)

        assert target["hp"] == 10
        assert any("自动失败" in line for line in result["lines"])


class TestDamageTypeAdjustments:
    """验证抗性、免疫、易伤统一在伤害管线生效。"""

    def test_damage_immunity_prevents_hp_loss(self):
        from app.services.tools._helpers import apply_damage_to_target

        target = {
            "id": "flameskull_1",
            "name": "Flameskull",
            "hp": 20,
            "max_hp": 20,
            "damage_immunities": ["fire"],
            "conditions": [],
        }

        damage, hp_change, lines = apply_damage_to_target(target, 10, damage_type="fire")

        assert damage == 0
        assert hp_change["new_hp"] == 20
        assert any("免疫" in line for line in lines)

    def test_damage_resistance_halves_hp_loss(self):
        from app.services.tools._helpers import apply_damage_to_target

        target = {
            "id": "wraith_1",
            "name": "Wraith",
            "hp": 30,
            "max_hp": 30,
            "damage_resistances": ["fire"],
            "conditions": [],
        }

        damage, hp_change, lines = apply_damage_to_target(target, 9, damage_type="火焰")

        assert damage == 4
        assert hp_change["new_hp"] == 26
        assert any("抗性" in line for line in lines)

    def test_damage_vulnerability_doubles_hp_loss(self):
        from app.services.tools._helpers import apply_damage_to_target

        target = {
            "id": "web_1",
            "name": "Web",
            "hp": 10,
            "max_hp": 10,
            "damage_vulnerabilities": ["fire"],
            "conditions": [],
        }

        damage, hp_change, lines = apply_damage_to_target(target, 4, damage_type="fire")

        assert damage == 8
        assert hp_change["new_hp"] == 2
        assert any("易伤" in line for line in lines)

    def test_damage_type_adjustment_happens_before_arcane_ward_absorption(self):
        from app.services.tools._helpers import apply_damage_to_target

        target = {
            "id": "warded_fire_immune",
            "name": "Warded Fire Immune",
            "hp": 20,
            "max_hp": 20,
            "damage_immunities": ["fire"],
            "conditions": [
                create_condition(
                    "arcane_ward",
                    extra=build_condition_extra(ward_hp=5),
                )
            ],
        }

        damage, hp_change, lines = apply_damage_to_target(target, 10, damage_type="fire")

        assert damage == 0
        assert hp_change["new_hp"] == 20
        assert target["conditions"][0]["extra"]["ward_hp"] == 5
        assert any("免疫" in line for line in lines)

    def test_spawned_monster_keeps_template_damage_defenses(self):
        from app.calculation.bestiary import spawn_combatants
        from app.services.open5e_client import MonsterTemplate

        template = MonsterTemplate(
            slug="resistant-test",
            name="Resistant Test",
            hit_dice="1d8",
            damage_resistances=["fire"],
            damage_immunities=["poison"],
            damage_vulnerabilities=["cold"],
        )

        with patch("app.calculation.bestiary.get_monster_template", return_value=template), patch(
            "app.calculation.bestiary.d20.roll",
            return_value=d20.roll("8"),
        ):
            combatant = spawn_combatants("resistant-test", 1)[0]

        assert combatant.damage_resistances == ["fire"]
        assert combatant.damage_immunities == ["poison"]
        assert combatant.damage_vulnerabilities == ["cold"]

    def test_spawned_monster_records_cr_xp_snapshot(self):
        from app.calculation.bestiary import spawn_combatants
        from app.services.open5e_client import MonsterTemplate

        template = MonsterTemplate(
            slug="goblin",
            name="Goblin",
            hit_dice="1d8",
            challenge_rating="1/4",
        )

        with patch("app.calculation.bestiary.get_monster_template", return_value=template), patch(
            "app.calculation.bestiary.d20.roll",
            return_value=d20.roll("8"),
        ):
            combatant = spawn_combatants("goblin", 1)[0]

        assert combatant.monster_slug == "goblin"
        assert combatant.challenge_rating == "1/4"
        assert combatant.xp_value == 50

    def test_spell_resolver_passes_damage_type_to_damage_pipeline(self):
        from app.spells import fire_bolt

        caster = _make_player_combatant("法师")
        target = _make_goblin()
        target["hp"] = 20
        target["max_hp"] = 20
        target["damage_immunities"] = ["fire"]
        real_roll = d20.roll

        with patch("app.spells._resolvers.d20.roll", side_effect=lambda expr: real_roll({"1d20+5": "15", "1d10": "7"}.get(expr, expr))):
            result = fire_bolt.execute(caster, [target], 0, cantrip_scale=1)

        assert target["hp"] == 20
        assert any("免疫" in line for line in result["lines"])

    def test_ice_knife_keeps_piercing_and_cold_adjustments_separate(self):
        from app.spells import ice_knife

        caster = _make_player_combatant("法师")
        target = _make_goblin()
        target["hp"] = 30
        target["max_hp"] = 30
        target["damage_immunities"] = ["cold"]

        mapping = {
            "1d20+5": "15",
            "1d10": "4",
            "2d6": "8",
            "1d20+2": "5",
        }
        real_roll = d20.roll
        with patch("app.spells.ice_knife.d20.roll", side_effect=lambda expr: real_roll(mapping.get(expr, expr))):
            result = ice_knife.execute(caster, [target], 1)

        assert target["hp"] == 26
        assert any("免疫" in line for line in result["lines"])
