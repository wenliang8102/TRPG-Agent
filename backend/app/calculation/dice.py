# 骰子投掷核心模块 — 基于 d20 库实现
from typing import Literal

import d20

from app.graph.state import RollResultState

# 内部 Roller 实例（自带 LFU 缓存）
_roller = d20.Roller()


def roll_dice(num_dice: int, sides: int) -> int:
    """基础掷骰，保留旧签名以兼容上层"""
    return d20.roll(f"{num_dice}d{sides}").total


def roll_d20(advantage: Literal["normal", "advantage", "disadvantage"] = "normal") -> int:
    """d20 检定掷骰，支持优势/劣势"""
    expr_map = {
        "advantage": "2d20kh1",
        "disadvantage": "2d20kl1",
    }
    return d20.roll(expr_map.get(advantage, "1d20")).total


def roll_expr(expression: str, advantage: Literal["normal", "advantage", "disadvantage"] = "normal") -> d20.RollResult:
    """直接返回 d20.RollResult 供上层获取 crit/表达式树等丰富信息"""
    adv_map = {
        "advantage": d20.AdvType.ADV,
        "disadvantage": d20.AdvType.DIS,
    }
    return _roller.roll(expression, advantage=adv_map.get(advantage, d20.AdvType.NONE))


def roll_with_notation(dice_notation: str) -> RollResultState:
    """掷骰并返回标准 RollResultState（兼容上层接口）"""
    result = d20.roll(dice_notation)

    # 从表达式树中分离"纯骰子值"和"固定修正值"
    # d20 的 result.total 已经是最终值；对于简单表达式如 "2d6+3"，可以倒推 raw
    # 简便做法：遍历 AST 统计 Literal 节点的固定加值
    modifier = _extract_modifier(result.expr)
    raw = result.total - modifier

    return RollResultState(
        dice=dice_notation,
        raw=raw,
        modifier=modifier,
        total=result.total,
        success=False,
    )


def _extract_modifier(node: d20.ast.Node) -> int:
    """从 d20 AST 中提取所有固定数值（非骰子）部分的总和"""
    if isinstance(node, d20.ast.Expression):
        return _extract_modifier(node.roll)
    if isinstance(node, d20.ast.Literal):
        return int(node.total)
    if isinstance(node, d20.ast.Dice):
        return 0  # 骰子部分不算 modifier
    if isinstance(node, d20.ast.UnOp):
        child = _extract_modifier(node.value)
        return -child if node.op == "-" else child
    if isinstance(node, d20.ast.BinOp):
        left = _extract_modifier(node.left)
        right = _extract_modifier(node.right)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        # * / 等运算符对普通骰子表达式极少见，直接返回 0
        return 0
    # Parenthetical / Set 等复合节点递归处理
    if isinstance(node, d20.ast.Parenthetical):
        return _extract_modifier(node.value)
    return 0