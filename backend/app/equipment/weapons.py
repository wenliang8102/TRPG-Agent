"""D&D 5e 武器规则表；覆盖《失落矿坑》会直接用到的武器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


WeaponType = Literal["melee", "ranged"]


@dataclass(frozen=True)
class WeaponSpec:
    """武器静态规则数据；角色自身属性只在进入战斗时再合成。"""

    name: str
    damage_dice: str
    damage_type: str
    weapon_type: WeaponType = "melee"
    properties: tuple[str, ...] = field(default_factory=tuple)
    normal_range_feet: int | None = None
    long_range_feet: int | None = None
    reach_feet: int = 5
    versatile_damage_dice: str | None = None
    attack_bonus: int = 0
    damage_bonus: int = 0
    extra_damage_dice: str = ""
    extra_damage_type: str = ""


def _w(
    name: str,
    damage_dice: str,
    damage_type: str,
    *,
    weapon_type: WeaponType = "melee",
    properties: tuple[str, ...] = (),
    normal_range_feet: int | None = None,
    long_range_feet: int | None = None,
    versatile_damage_dice: str | None = None,
    attack_bonus: int = 0,
    damage_bonus: int = 0,
    extra_damage_dice: str = "",
    extra_damage_type: str = "",
) -> WeaponSpec:
    """让武器表保持接近 SRD 表格的扁平形态。"""

    return WeaponSpec(
        name=name,
        damage_dice=damage_dice,
        damage_type=damage_type,
        weapon_type=weapon_type,
        properties=properties,
        normal_range_feet=normal_range_feet,
        long_range_feet=long_range_feet,
        versatile_damage_dice=versatile_damage_dice,
        attack_bonus=attack_bonus,
        damage_bonus=damage_bonus,
        extra_damage_dice=extra_damage_dice,
        extra_damage_type=extra_damage_type,
    )


WEAPON_SPECS: dict[str, WeaponSpec] = {
    "club": _w("Club", "1d4", "bludgeoning", properties=("light",)),
    "dagger": _w("Dagger", "1d4", "piercing", properties=("finesse", "light", "thrown"), normal_range_feet=20, long_range_feet=60),
    "greatclub": _w("Greatclub", "1d8", "bludgeoning", properties=("two-handed",)),
    "handaxe": _w("Handaxe", "1d6", "slashing", properties=("light", "thrown"), normal_range_feet=20, long_range_feet=60),
    "javelin": _w("Javelin", "1d6", "piercing", properties=("thrown",), normal_range_feet=30, long_range_feet=120),
    "mace": _w("Mace", "1d6", "bludgeoning"),
    "quarterstaff": _w("Quarterstaff", "1d6", "bludgeoning", properties=("versatile",), versatile_damage_dice="1d8"),
    "shortbow": _w("Shortbow", "1d6", "piercing", weapon_type="ranged", properties=("ammunition", "two-handed"), normal_range_feet=80, long_range_feet=320),
    "battleaxe": _w("Battleaxe", "1d8", "slashing", properties=("versatile",), versatile_damage_dice="1d10"),
    "greataxe": _w("Greataxe", "1d12", "slashing", properties=("heavy", "two-handed")),
    "longsword": _w("Longsword", "1d8", "slashing", properties=("versatile",), versatile_damage_dice="1d10"),
    "morningstar": _w("Morningstar", "1d8", "piercing"),
    "rapier": _w("Rapier", "1d8", "piercing", properties=("finesse",)),
    "scimitar": _w("Scimitar", "1d6", "slashing", properties=("finesse", "light")),
    "shortsword": _w("Shortsword", "1d6", "piercing", properties=("finesse", "light")),
    "light-crossbow": _w("Light Crossbow", "1d8", "piercing", weapon_type="ranged", properties=("ammunition", "loading", "two-handed"), normal_range_feet=80, long_range_feet=320),
    "heavy-crossbow": _w("Heavy Crossbow", "1d10", "piercing", weapon_type="ranged", properties=("ammunition", "heavy", "loading", "two-handed"), normal_range_feet=100, long_range_feet=400),
    "longbow": _w("Longbow", "1d8", "piercing", weapon_type="ranged", properties=("ammunition", "heavy", "two-handed"), normal_range_feet=150, long_range_feet=600),
    "talon": _w("Talon", "1d8", "slashing", properties=("versatile", "magic"), versatile_damage_dice="1d10", attack_bonus=1, damage_bonus=1),
    "hew": _w("Hew", "1d8", "slashing", properties=("versatile", "magic"), versatile_damage_dice="1d10", attack_bonus=1, damage_bonus=1),
    "lightbringer": _w("Lightbringer", "1d6", "bludgeoning", properties=("magic",), attack_bonus=1, damage_bonus=1, extra_damage_dice="1d6", extra_damage_type="radiant"),
    "spider-staff": _w("Spider Staff", "1d6", "bludgeoning", properties=("versatile", "magic"), versatile_damage_dice="1d8", extra_damage_dice="1d6", extra_damage_type="poison"),
}

WEAPON_ALIASES: dict[str, str] = {
    "heavy crossbow": "heavy-crossbow",
    "spider staff": "spider-staff",
    "巨棒": "greatclub",
    "标枪": "javelin",
    "硬头锤": "mace",
    "长棍": "quarterstaff",
    "短弓": "shortbow",
    "战斧": "battleaxe",
    "巨斧": "greataxe",
    "长剑": "longsword",
    "钉头锤": "morningstar",
    "弯刀": "scimitar",
    "短剑": "shortsword",
    "轻弩": "light-crossbow",
    "light crossbow": "light-crossbow",
    "重弩": "heavy-crossbow",
    "长弓": "longbow",
    "蜘蛛法杖": "spider-staff",
    "利爪": "talon",
    "砍伐者": "hew",
    "光明使者": "lightbringer",
}


def canonical_weapon_id(name: str) -> str:
    """把英文、中文和空格写法都收敛到武器表主键。"""

    text = str(name or "").strip()
    lowered = text.lower().replace("_", "-")
    dashed = "-".join(lowered.split())
    return WEAPON_ALIASES.get(text) or WEAPON_ALIASES.get(lowered) or WEAPON_ALIASES.get(dashed) or dashed


def get_weapon_spec(name: str) -> WeaponSpec | None:
    """按名称或别名查找武器规则。"""

    return WEAPON_SPECS.get(canonical_weapon_id(name))


def resolve_weapon_data(raw_weapon: dict) -> dict:
    """用武器表补齐角色卡里的轻量武器声明，并允许角色卡显式覆盖。"""

    spec = get_weapon_spec(str(raw_weapon.get("id") or raw_weapon.get("name") or ""))
    resolved = _weapon_spec_to_dict(spec) if spec else {}
    for key, value in raw_weapon.items():
        if value in (None, ""):
            continue
        if key == "properties" and value == [] and key in resolved:
            continue
        resolved[key] = value
    if "id" not in resolved:
        resolved["id"] = canonical_weapon_id(str(resolved.get("name", "")))
    return resolved


def _weapon_spec_to_dict(spec: WeaponSpec | None) -> dict:
    """把不可变规则对象转成现有状态系统使用的普通 dict。"""

    if spec is None:
        return {}
    return {
        "id": canonical_weapon_id(spec.name),
        "name": spec.name,
        "damage_dice": spec.damage_dice,
        "damage_type": spec.damage_type,
        "weapon_type": spec.weapon_type,
        "properties": list(spec.properties),
        "normal_range_feet": spec.normal_range_feet,
        "long_range_feet": spec.long_range_feet,
        "reach_feet": spec.reach_feet,
        "versatile_damage_dice": spec.versatile_damage_dice,
        "attack_bonus": spec.attack_bonus,
        "damage_bonus": spec.damage_bonus,
        "extra_damage_dice": spec.extra_damage_dice,
        "extra_damage_type": spec.extra_damage_type,
    }
