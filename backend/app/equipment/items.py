"""消耗品定义 — 冒险模组道具只在这里扩展。"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel


ItemKind = Literal["potion", "item", "treasure"]


class ConsumableItem(BaseModel):
    """可使用道具的最小规则契约。"""
    id: str
    name: str
    name_en: str
    kind: ItemKind = "potion"
    description: str
    price_gp: int
    default_quantity: int = 1


CONSUMABLE_ITEMS: dict[str, ConsumableItem] = {
    "potion_of_healing": ConsumableItem(
        id="potion_of_healing",
        name="治疗药水",
        name_en="Potion of Healing",
        description="饮用此药水可以恢复2d4+2点生命值。",
        price_gp=50,
    ),
    "potion_of_greater_healing": ConsumableItem(
        id="potion_of_greater_healing",
        name="强效治疗药水",
        name_en="Potion of Greater Healing",
        description="饮用此药水可以恢复4d4+4点生命值。",
        price_gp=200,
    ),
    "potion_of_invisibility": ConsumableItem(
        id="potion_of_invisibility",
        name="隐身药水",
        name_en="Potion of Invisibility",
        description="饮用后隐形1小时；攻击或施法会提前终止。",
        price_gp=200,
    ),
    "potion_of_vitality": ConsumableItem(
        id="potion_of_vitality",
        name="活力药水",
        name_en="Potion of Vitality",
        description="消除力竭，并治愈疾病或毒素；24小时内生命骰治疗取最大值。",
        price_gp=200,
    ),
}


def get_consumable_item(item_id: str) -> ConsumableItem:
    """按稳定道具 ID 读取规则定义。"""
    return CONSUMABLE_ITEMS[str(item_id).strip()]


def create_inventory_item(item_id: str, quantity: int = 1) -> dict:
    """创建角色背包条目，统一字段供前端和工具消费。"""
    item = get_consumable_item(item_id)
    return {
        "id": item.id,
        "name": item.name,
        "type": item.kind,
        "quantity": quantity,
        "description": item.description,
        "price_gp": item.price_gp,
    }


def default_potion_inventory() -> list[dict]:
    """开局防暴毙：玩家和初始友方默认各带两瓶治疗药水。"""
    return [deepcopy(create_inventory_item("potion_of_healing", 2))]
