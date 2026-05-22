"""项目内技能注册表，用于实现工具使用说明的渐进式披露。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """技能索引只保留必要元信息，避免基础提示词膨胀。"""

    skill_id: str
    name: str
    description: str
    path: Path


_SKILL_ROOT = Path(__file__).resolve().parent

_SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        skill_id="character_state_management",
        name="角色状态调整",
        description="说明 HP/AC/资源/状态效果等角色状态调整流程。",
        path=_SKILL_ROOT / "character_state_management" / "SKILL.md",
    ),
    SkillSpec(
        skill_id="character_progression",
        name="角色成长与子职",
        description="说明经验、升级、法师奥术传承和战士武术范型等成长流程。",
        path=_SKILL_ROOT / "character_progression" / "SKILL.md",
    ),
    SkillSpec(
        skill_id="class_actions",
        name="职业动作",
        description="说明回气、动作如潮、战斗大师战技选择与战技使用流程。",
        path=_SKILL_ROOT / "class_actions" / "SKILL.md",
    ),
    SkillSpec(
        skill_id="space_management",
        name="平面空间管理",
        description="说明地图创建、切换、单位落点、战斗移动、测距和范围查询流程。",
        path=_SKILL_ROOT / "space_management" / "SKILL.md",
    ),
    SkillSpec(
        skill_id="scene_unit_management",
        name="场景单位管理",
        description="说明友方创建、怪物生成和死亡单位档案清理流程。",
        path=_SKILL_ROOT / "scene_unit_management" / "SKILL.md",
    ),
    SkillSpec(
        skill_id="adventure_module",
        name="冒险模组主持",
        description="说明 PDF 冒险节点读取、线索记录、事件记录和剧情书签推进流程。",
        path=_SKILL_ROOT / "adventure_module" / "SKILL.md",
    ),
)


def get_skill_index() -> list[SkillSpec]:
    """返回模型可见的轻量技能索引。"""
    return list(_SKILLS)


@lru_cache(maxsize=None)
def load_skill_content(skill_id: str) -> str:
    """按 ID 读取完整技能内容，让复杂工具说明按需进入上下文。"""
    matches = [skill for skill in _SKILLS if skill.skill_id == skill_id]
    if not matches:
        valid_ids = ", ".join(skill.skill_id for skill in _SKILLS)
        raise ValueError(f"Unknown skill_id: {skill_id}. Available skills: {valid_ids}")
    return matches[0].path.read_text(encoding="utf-8")
