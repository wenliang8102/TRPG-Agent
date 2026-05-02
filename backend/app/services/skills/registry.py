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
        description="当需要调整 HP/AC/资源/经验/升级/法师学派/状态效果时，先加载此技能再调用 modify_character_state。",
        path=_SKILL_ROOT / "character_state_management" / "SKILL.md",
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
