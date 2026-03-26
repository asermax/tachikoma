"""Skills package: skill discovery, agent loading, and bootstrap hook.

Provides the SkillRegistry for discovering and loading skills and their
agents at startup, plus the bootstrap hook for initializing the skills
directory structure. Includes filesystem watcher for hot-reloading.
"""

from tachikoma.skills.context_provider import SkillsContextProvider
from tachikoma.skills.events import SkillsChanged
from tachikoma.skills.hooks import skills_hook
from tachikoma.skills.registry import Skill, SkillRegistry
from tachikoma.skills.watcher import watch_skills

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillsChanged",
    "SkillsContextProvider",
    "skills_hook",
    "watch_skills",
]
