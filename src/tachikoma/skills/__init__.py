"""Skills package: skill discovery, agent loading, and bootstrap hook.

Provides the SkillRegistry for discovering and loading skills and their
agents at startup, plus the bootstrap hook for initializing the skills
directory structure.
"""

from tachikoma.skills.hooks import skills_hook
from tachikoma.skills.registry import Skill, SkillRegistry

__all__ = [
    "Skill",
    "SkillRegistry",
    "skills_hook",
]
