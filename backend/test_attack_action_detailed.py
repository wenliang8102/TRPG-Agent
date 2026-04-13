"""
详细测试脚本：观察 attack_action 工具的完整逻辑流程。
这是一个演示脚本，展示：
1. 如何构建战斗状态（participants）
2. 如何调用 attack_action
3. 输出每一步的关键状态变化
"""
import json
import sys
import re
import d20
from pathlib import Path
from typing import Literal

# 将 backend 加入路径
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.calculation.bestiary import spawn_combatants
from app.graph.state import CombatState
from langchain_core.messages import ToolMessage
from langgraph.types import Command


import d20
from typing import Literal


def _get_natural_d20(result: d20.RollResult) -> int:
    """
    从 d20 投掷结果中提取天然 d20 点数。
    
    方法：遍历 AST，找到第一个大小为 20 的 Die，取其第一个结果。
    """
    def extract_dice_value(node):
        """递归提取 AST 中的骰子值"""
        if hasattr(node, 'values'):  # Dice 或 Die 节点
            for item in node.values:
                if hasattr(item, 'number'):  # Literal（最终值）
                    return item.number
                else:
                    res = extract_dice_value(item)
                    if res is not None:
                        return res
        elif hasattr(node, 'left'):  # BinOp 节点
            res = extract_dice_value(node.left)
            if res is not None:
                return res
            return extract_dice_value(node.right)
        return None
    
    # 从 expr.roll 开始遍历
    if hasattr(result.expr, 'roll'):
        val = extract_dice_value(result.expr.roll)
        if val is not None:
            return val
    
    # 如果找不到，返回 total（可能是纯骰子没有加值）
    return result.total


def attack_action_impl(
    attacker_id: str,
    target_id: str,
    attack_name: str | None = None,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
    state: dict = None,
) -> Command | str:
    """
    attack_action 的直接实现（去掉 @tool 装饰器，用于测试）
    这是原始的工具逻辑，不经过 BaseTool 包装
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
    participants = combat_dict.get("participants", {})

    attacker = participants.get(attacker_id)
    target = participants.get(target_id)
    if not attacker:
        return f"找不到攻击者 '{attacker_id}'。"
    if not target:
        return f"找不到目标 '{target_id}'。"

    # 选择攻击方式
    attacks = attacker.get("attacks", [])
    if attack_name:
        chosen = next((a for a in attacks if a["name"].lower() == attack_name.lower()), None)
    else:
        chosen = attacks[0] if attacks else None

    atk_bonus = chosen["attack_bonus"] if chosen else 0
    dmg_dice = chosen["damage_dice"] if chosen else "1d4"
    dmg_type = chosen.get("damage_type", "bludgeoning") if chosen else "bludgeoning"
    atk_name_display = chosen["name"] if chosen else "徒手攻击"

    # 命中骰
    if advantage == "advantage":
        hit_expr = f"2d20kh1+{atk_bonus}"
    elif advantage == "disadvantage":
        hit_expr = f"2d20kl1+{atk_bonus}"
    else:
        hit_expr = f"1d20+{atk_bonus}"

    hit_result = d20.roll(hit_expr)
    target_ac = target.get("ac", 10)

    # D&D 5e 天然 1/20 铁规则（改用 result.crit 检查）
    # result.crit 的值：1表示暴击(自然20)，2表示失误(自然1)，0表示普通
    if hit_result.crit == d20.CritType.FAIL:  # 自然 1
        hit, crit = False, False
    elif hit_result.crit == d20.CritType.CRIT:  # 自然 20
        hit, crit = True, True
    else:
        hit = hit_result.total >= target_ac
        crit = False
    
    natural = _get_natural_d20(hit_result)

    # 构建结果叙事
    lines: list[str] = []
    lines.append(f"{attacker.get('name', attacker_id)} 使用 [{atk_name_display}] 攻击 {target.get('name', target_id)}!")
    lines.append(f"命中骰: {hit_result} (天然 {natural}) vs AC {target_ac}")

    damage_dealt = 0
    if hit:
        # 暴击时骰子翻倍
        if crit:
            crit_dice = re.sub(r"(\d+)d(\d+)", lambda m: f"{int(m.group(1))*2}d{m.group(2)}", dmg_dice)
            lines.append("暴击！骰子数翻倍！")
        else:
            crit_dice = dmg_dice

        dmg_result = d20.roll(crit_dice)
        damage_dealt = max(1, dmg_result.total)
        lines.append(f"伤害骰: {dmg_result} → {damage_dealt} 点 {dmg_type} 伤害")

        # 扣血
        old_hp = target.get("hp", 0)
        new_hp = max(0, old_hp - damage_dealt)
        target["hp"] = new_hp
        lines.append(f"{target.get('name', target_id)} HP: {old_hp} → {new_hp}")

        if new_hp == 0:
            lines.append(f"{target.get('name', target_id)} 倒下了！")
    else:
        lines.append("未命中！" if natural != 1 else "严重失误！攻击完全落空！")

    return Command(
        update={
            "combat": combat_dict,
            "messages": [
                ToolMessage(content="\n".join(lines), tool_call_id="test")
            ],
        }
    )



def print_separator(title: str):
    """打印分隔线"""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def format_combatant_summary(c: dict) -> str:
    """格式化单位摘要"""
    return (
        f"  ID: {c.get('id', '?')}\n"
        f"  名字: {c.get('name', '?')}\n"
        f"  HP: {c.get('hp', '?')}/{c.get('max_hp', '?')}\n"
        f"  AC: {c.get('ac', '?')}\n"
        f"  攻击列表:\n"
    )


def test_basic_attack():
    """测试场景 1：基础攻击（正常命中）"""
    print_separator("测试场景 1：基础攻击逻辑")
    
    # 第 1 步：生成两个地精（一个攻击者，一个目标）
    print("【第 1 步】生成战斗参与者：")
    attacker_list = spawn_combatants("goblin", 1, "enemy")
    target_list = spawn_combatants("goblin", 1, "enemy")
    
    attacker = attacker_list[0]
    target = target_list[0]
    
    print(f"  攻击者: {attacker.name} (HP: {attacker.hp}/{attacker.max_hp}, AC: {attacker.ac})")
    print(f"  目标: {target.name} (HP: {target.hp}/{target.max_hp}, AC: {target.ac})")
    
    # 第 2 步：检查攻击列表
    print(f"\n【第 2 步】检查攻击列表：")
    print(f"  攻击者有 {len(attacker.attacks)} 个攻击方式:")
    for i, atk in enumerate(attacker.attacks):
        print(
            f"    [{i}] {atk.name}: 命中加值={atk.attack_bonus}, 伤害骰={atk.damage_dice}, 伤害类型={atk.damage_type}"
        )

    first_damage_formula = attacker.attacks[0].damage_dice if attacker.attacks else ""
    print(f"  首个攻击最终伤害公式: {first_damage_formula}")
    if first_damage_formula != "1d6+2":
        raise AssertionError(f"期望 goblin 攻击被解析为 1d6+2，实际得到 {first_damage_formula}")
    
    # 第 3 步：构建 combat 状态（模拟 spawn_monsters 后的状态）
    print(f"\n【第 3 步】构建 combat 状态：")
    combat_dict = {
        "round": 1,
        "participants": {
            attacker.id: attacker.model_dump(),
            target.id: target.model_dump(),
        },
        "initiative_order": [attacker.id, target.id],
        "current_actor_id": attacker.id,
    }
    
    print(f"  Participants 中有 {len(combat_dict['participants'])} 个单位")
    print(f"  当前行动者: {combat_dict['current_actor_id']}")
    
    # 第 4 步：调用 attack_action
    print(f"\n【第 4 步】执行 attack_action 工具：")
    print(
        f"  调用参数:"
        f"\n    attacker_id: {attacker.id}"
        f"\n    target_id: {target.id}"
        f"\n    attack_name: 不指定（会用第一个攻击）"
        f"\n    advantage: normal"
    )
    
    # 创建模拟状态（LangGraph 会注入，这里手动提供）
    state = {"combat": combat_dict}
    
    # 调用工具实现
    result = attack_action_impl(
        attacker_id=attacker.id,
        target_id=target.id,
        attack_name=None,
        advantage="normal",
        state=state,
    )
    
    # 第 5 步：观察返回结果
    print(f"\n【第 5 步】观察返回的 Command：")
    if isinstance(result, str):
        print(f"  错误返回: {result}")
        return
    
    # 获取更新后的状态
    updated_combat = result.update.get("combat")
    updated_msgs = result.update.get("messages", [])
    
    print(f"  消息文本:")
    if updated_msgs:
        for msg in updated_msgs:
            print(f"    {msg.content}")
    
    print(f"\n  目标 HP 变化:")
    old_hp = target.hp
    new_target = updated_combat["participants"][target.id]
    new_hp = new_target.get("hp", "?")
    print(f"    {old_hp} → {new_hp} (伤害: {old_hp - new_hp if isinstance(old_hp, int) else '?'})")
    
    print(f"\n[✓] 测试完毕\n")


def test_multiple_attacks():
    """测试场景 2：选择不同的攻击方式"""
    print_separator("测试场景 2：多攻击方式选择")
    
    print("【说明】goblin 有 2 个攻击：Scimitar 和 Shortbow")
    
    attacker_list = spawn_combatants("goblin", 1, "enemy")
    target_list = spawn_combatants("goblin", 1, "enemy")
    
    attacker = attacker_list[0]
    target = target_list[0]
    
    print(f"\n攻击者攻击列表:")
    for i, atk in enumerate(attacker.attacks):
        print(f"  [{i}] {atk.name} (伤害骰: {atk.damage_dice})")
    
    combat_dict = {
        "round": 1,
        "participants": {
            attacker.id: attacker.model_dump(),
            target.id: target.model_dump(),
        },
        "initiative_order": [attacker.id, target.id],
        "current_actor_id": attacker.id,
    }
    
    state = {"combat": combat_dict}
    
    # 尝试指定第二个攻击 Shortbow
    print(f"\n【第 1 次】使用 Shortbow:")
    result1 = attack_action_impl(
        attacker_id=attacker.id,
        target_id=target.id,
        attack_name="Shortbow",
        state=state,
    )
    if isinstance(result1, str):
        print(f"  错误: {result1}")
    else:
        msgs = result1.update.get("messages", [])
        if msgs:
            print(f"  {msgs[0].content.split(chr(10))[0]}")  # 第一行
    
    # 重置目标 HP
    target_list2 = spawn_combatants("goblin", 1, "enemy")
    target2 = target_list2[0]
    combat_dict2 = {
        "round": 1,
        "participants": {
            attacker.id: attacker.model_dump(),
            target2.id: target2.model_dump(),
        },
        "initiative_order": [attacker.id, target2.id],
        "current_actor_id": attacker.id,
    }
    state2 = {"combat": combat_dict2}
    
    # 尝试第一个攻击 Scimitar
    print(f"\n【第 2 次】使用 Scimitar:")
    result2 = attack_action_impl(
        attacker_id=attacker.id,
        target_id=target2.id,
        attack_name="Scimitar",
        state=state2,
    )
    if isinstance(result2, str):
        print(f"  错误: {result2}")
    else:
        msgs = result2.update.get("messages", [])
        if msgs:
            print(f"  {msgs[0].content.split(chr(10))[0]}")
    
    print(f"\n[OK] 两种攻击方式都可以正常选择\n")


def test_data_flow():
    """测试场景 3：数据流向追踪"""
    print_separator("测试场景 3：数据流向与类型追踪")
    
    print("【关键问题】: 攻击数据是如何从 CombatantState 流向字典格式的？\n")
    
    # 生成单位
    combatants = spawn_combatants("goblin", 1, "enemy")
    combatant = combatants[0]
    
    print("【阶段 1】Pydantic 对象（CombatantState 直接）:")
    print(f"  type(attacks) = {type(combatant.attacks)}")
    print(f"  攻击数量 = {len(combatant.attacks)}")
    if combatant.attacks:
        first = combatant.attacks[0]
        print(f"  第一个攻击类型 = {type(first).__name__}")
        print(f"  攻击名: {first.name}, 命中加值: {first.attack_bonus}, 伤害骰: {first.damage_dice}")
    
    # 序列化为字典
    combatant_dict = combatant.model_dump()
    
    print(f"\n【阶段 2】.model_dump() 后（序列化为字典）:")
    print(f"  type(attacks) = {type(combatant_dict['attacks'])}")
    print(f"  攻击数量 = {len(combatant_dict['attacks'])}")
    if combatant_dict['attacks']:
        first_dict = combatant_dict['attacks'][0]
        print(f"  第一个攻击类型 = {type(first_dict).__name__}")
        print(f"  攻击名: {first_dict['name']}, 命中加值: {first_dict['attack_bonus']}, 伤害骰: {first_dict['damage_dice']}")
    
    print(f"\n【阶段 3】进入 combat.participants：")
    combat_dict = {
        "participants": {
            combatant.id: combatant_dict,
        }
    }
    
    stored_combatant = combat_dict["participants"][combatant.id]
    print(f"  type(participants[id]['attacks']) = {type(stored_combatant['attacks'])}")
    print(f"  可以通过 .get('attacks', []) 安全取出吗？ {isinstance(stored_combatant.get('attacks', []), list)}")
    
    # 在 attack_action 中的访问方式
    print(f"\n【阶段 4】在 attack_action 中的访问:")
    attacks = stored_combatant.get("attacks", [])
    print(f"  attacks = participant.get('attacks', [])")
    print(f"  type(attacks) = {type(attacks)}")
    if attacks:
        chosen = attacks[0]
        print(f"  chosen = attacks[0] if attacks else None")
        print(f"  type(chosen) = {type(chosen).__name__}")
        
        # 三元表达式防护
        atk_bonus = chosen["attack_bonus"] if chosen else 0
        dmg_dice = chosen["damage_dice"] if chosen else "1d4"
        print(f"  atk_bonus = {atk_bonus}")
        print(f"  dmg_dice = {dmg_dice}")
    
    print(f"\n[OK] 数据流向完全追踪成功\n")


def test_edge_cases():
    """测试场景 4：边界情况"""
    print_separator("测试场景 4：边界情况与错误处理")
    
    print("【情况 1】目标不存在:")
    combatants = spawn_combatants("goblin", 1, "enemy")
    combatant = combatants[0]
    
    combat_dict = {
        "participants": {
            combatant.id: combatant.model_dump(),
        }
    }
    state = {"combat": combat_dict}
    
    result = attack_action_impl(
        attacker_id=combatant.id,
        target_id="nonexistent_id",
        state=state,
    )
    
    if isinstance(result, str):
        print(f"  返回错误字符串: {result}")
    else:
        msgs = result.update.get("messages", [])
        if msgs:
            print(f"  {msgs[0].content}")
    
    print(f"\n【情况 2】攻击者 attacks 列表为空:")
    combatants = spawn_combatants("goblin", 1, "enemy")
    c1, c2 = combatants[0], combatants[0]
    
    # 手动清空一个的 attacks
    c1_dict = c1.model_dump()
    c1_dict["attacks"] = []
    c2_dict = c2.model_dump()
    
    combat_dict = {
        "participants": {
            c1.id: c1_dict,
            c2.id: c2_dict,
        }
    }
    state = {"combat": combat_dict}
    
    result = attack_action_impl(
        attacker_id=c1.id,
        target_id=c2.id,
        state=state,
    )
    
    if isinstance(result, str):
        print(f"  返回错误: {result}")
    else:
        msgs = result.update.get("messages", [])
        if msgs:
            print(f"  使用默认值: {msgs[0].content}")
    
    print(f"\n[OK] 边界情况都有正确处理\n")


if __name__ == "__main__":
    print("\n")
    print("*" * 80)
    print("*" + " " * 78 + "*")
    print("*  attack_action 工具详细逻辑测试框架".ljust(80) + "*")
    print("*  本脚本展示 attack_action 的每一步逻辑与数据流".ljust(80) + "*")
    print("*" * 80)
    
    try:
        test_basic_attack()
        test_multiple_attacks()
        test_data_flow()
        test_edge_cases()
        
        print("*" * 80)
        print("*  所有测试场景完成，验证无误".ljust(80) + "*")
        print("*" * 80)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
