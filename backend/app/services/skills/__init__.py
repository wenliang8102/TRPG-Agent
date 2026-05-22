"""技能注册入口：只暴露轻量索引，完整内容由 load_skill 按需读取。"""

from app.services.skills.registry import SkillSpec, get_skill_index, load_skill_content

__all__ = ["SkillSpec", "get_skill_index", "load_skill_content"]
