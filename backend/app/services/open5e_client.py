# Open5e REST API 客户端 — 替代本地 bestiary.json
from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from app.graph.state import AttackInfo

_BASE_URL = "https://api.open5e.com/v1"

# 内存缓存：slug → MonsterTemplate，避免重复请求
_cache: dict[str, MonsterTemplate] = {}


class MonsterTemplate(BaseModel):
    """从 Open5e v1 API 解析的怪物模板"""
    slug: str
    name: str
    size: str = "Medium"
    type: str = "beast"
    armor_class: int = 10
    hit_points: int = 10
    hit_dice: str = "1d8"
    speed_walk: int = 30
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10
    challenge_rating: str = "0"
    proficiency_bonus: int = 2
    attacks: list[AttackInfo] = Field(default_factory=list)


def _parse_speed(speed: dict) -> int:
    """从 Open5e speed 字段提取步行速度"""
    walk = speed.get("walk")
    if isinstance(walk, (int, float)):
        return int(walk)
    if isinstance(walk, str):
        import re
        m = re.search(r"(\d+)", walk)
        return int(m.group(1)) if m else 30
    return 30


def _parse_attacks(actions: list[dict] | None) -> list[AttackInfo]:
    """从 Open5e actions 中提取有 attack_bonus 的攻击动作"""
    if not actions:
        return []
    result = []
    for action in actions:
        bonus = action.get("attack_bonus")
        if bonus is None:
            continue

        # Open5e 可能直接给出完整公式，也可能拆成 damage_dice + damage_bonus。
        damage_dice = _build_damage_formula(action)
        result.append(AttackInfo(
            name=action.get("name", "Attack"),
            attack_bonus=int(bonus),
            damage_dice=damage_dice,
            damage_type=_extract_damage_type(action),
        ))
    return result


def _build_damage_formula(action: dict) -> str:
    """将 Open5e 的伤害字段归一成 d20 可执行公式。"""
    damage_dice = str(action.get("damage_dice") or "1d4").replace(" ", "")
    damage_bonus = action.get("damage_bonus")
    if damage_bonus in (None, "", 0, "0"):
        return damage_dice

    # 已经是完整公式时不再重复拼接常数项。
    if "+" in damage_dice or "-" in damage_dice[1:]:
        return damage_dice

    bonus_value = int(damage_bonus)
    if bonus_value == 0:
        return damage_dice
    if bonus_value > 0:
        return f"{damage_dice}+{bonus_value}"
    return f"{damage_dice}{bonus_value}"


def _extract_damage_type(action: dict) -> str:
    """尝试从 action 描述中提取伤害类型"""
    # Open5e v1 把伤害类型写在 desc 里，如 "... 7 (1d8 + 3) slashing damage."
    desc = action.get("desc", "")
    known_types = [
        "slashing", "piercing", "bludgeoning", "fire", "cold", "lightning",
        "thunder", "poison", "acid", "necrotic", "radiant", "force", "psychic",
    ]
    desc_lower = desc.lower()
    for dt in known_types:
        if dt in desc_lower:
            return dt
    return "bludgeoning"


def _build_template(data: dict) -> MonsterTemplate:
    """将 Open5e v1 原始响应转为 MonsterTemplate"""
    speed = data.get("speed", {})
    prof = data.get("proficiency_bonus")
    if prof is None:
        # Open5e v1 某些怪物不提供 proficiency_bonus，用 CR 估算
        prof = 2

    return MonsterTemplate(
        slug=data.get("slug", ""),
        name=data.get("name", "Unknown"),
        size=data.get("size", "Medium"),
        type=data.get("type", "beast"),
        armor_class=data.get("armor_class", 10),
        hit_points=data.get("hit_points", 10),
        hit_dice=data.get("hit_dice", "1d8"),
        speed_walk=_parse_speed(speed),
        strength=data.get("strength", 10),
        dexterity=data.get("dexterity", 10),
        constitution=data.get("constitution", 10),
        intelligence=data.get("intelligence", 10),
        wisdom=data.get("wisdom", 10),
        charisma=data.get("charisma", 10),
        challenge_rating=str(data.get("challenge_rating", "0")),
        proficiency_bonus=int(prof),
        attacks=_parse_attacks(data.get("actions")),
    )


def get_monster_template(slug: str) -> MonsterTemplate:
    """
    通过 Open5e v1 API 获取怪物数据。首选 5e SRD 来源。
    结果会缓存在内存中。
    
    Raises:
        ValueError: 怪物不存在或 API 不可达
    """
    if slug in _cache:
        return _cache[slug]

    url = f"{_BASE_URL}/monsters/{slug}/"
    try:
        resp = httpx.get(url, params={"format": "json"}, timeout=10)
    except httpx.HTTPError as e:
        raise ValueError(f"Open5e API 请求失败: {e}") from e

    if resp.status_code == 404:
        raise ValueError(f"Monster '{slug}' not found on Open5e.")
    resp.raise_for_status()

    template = _build_template(resp.json())
    _cache[slug] = template
    return template


def search_monsters(name: str, limit: int = 5) -> list[dict]:
    """按名称模糊搜索怪物，返回简要列表"""
    url = f"{_BASE_URL}/monsters/"
    resp = httpx.get(url, params={"search": name, "limit": limit, "format": "json"}, timeout=10)
    resp.raise_for_status()
    return [{"slug": m["slug"], "name": m["name"], "cr": m.get("challenge_rating")} for m in resp.json().get("results", [])]
