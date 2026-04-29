"""Plugin system for extending Tachikoma with third-party skills.

Public surface is kept minimal here to avoid circular imports. Import
specific submodules directly for heavy objects (manager, loader, hooks).
"""

from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.plugins.sources import (
    GitPluginSource,
    LocalPluginSource,
    PluginSource,
    UrlPluginSource,
)

__all__ = [
    "GitPluginSource",
    "LocalPluginSource",
    "PluginInstalled",
    "PluginRemoved",
    "PluginRemoving",
    "PluginSource",
    "UrlPluginSource",
]
