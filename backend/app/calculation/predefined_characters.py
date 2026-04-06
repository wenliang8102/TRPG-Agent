from typing import Dict

from app.graph.state import PlayerState

# 预设角色卡模板库
# 使用硬编码字典用于基础测试和快速起步，对应 state.PlayerState 结构
PREDEFINED_CHARACTERS: Dict[str, dict] = {
    "战士": {
        "name": "预设-战士",
        "role_class": "战士",
        "level": 1,
        "hp": 12,
        "max_hp": 12,
        "temp_hp": 0,
        "ac": 16,
        "abilities": {
            "str": 16,
            "dex": 14,
            "con": 15,
            "int": 10,
            "wis": 12,
            "cha": 8,
        },
        "modifiers": {
            "str": 3,
            "dex": 2,
            "con": 2,
            "int": 0,
            "wis": 1,
            "cha": -1,
        },
        "conditions": [],
        "resources": {},
    },
    "法师": {
        "name": "预设-法师",
        "role_class": "法师",
        "level": 1,
        "hp": 8,
        "max_hp": 8,
        "temp_hp": 0,
        "ac": 12,
        "abilities": {
            "str": 8,
            "dex": 14,
            "con": 14,
            "int": 16,
            "wis": 12,
            "cha": 10,
        },
        "modifiers": {
            "str": -1,
            "dex": 2,
            "con": 2,
            "int": 3,
            "wis": 1,
            "cha": 0,
        },
        "conditions": [],
        "resources": {"spell_slot_lv1": 2},
    },
    "游荡者": {
        "name": "预设-游荡者",
        "role_class": "游荡者",
        "level": 1,
        "hp": 10,
        "max_hp": 10,
        "temp_hp": 0,
        "ac": 14,
        "abilities": {
            "str": 10,
            "dex": 16,
            "con": 14,
            "int": 12,
            "wis": 10,
            "cha": 14,
        },
        "modifiers": {
            "str": 0,
            "dex": 3,
            "con": 2,
            "int": 1,
            "wis": 0,
            "cha": 2,
        },
        "conditions": [],
        "resources": {},
    },
}
