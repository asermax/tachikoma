"""Tests for plugins package public surface (DLT-048 Batch 7, Step 7.4).

Verifies that re-exported names are importable from ``tachikoma.plugins``
and that submodules are importable directly.
"""

from tachikoma.plugins import (
    GitPluginSource,
    LocalPluginSource,
    PluginInstalled,
    PluginRemoved,
    PluginRemoving,
    PluginSource,
    UrlPluginSource,
)
from tachikoma.plugins.hooks import plugins_hook
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manager import PluginManager
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.tools import create_plugin_tools_server


def test_public_imports_from_package() -> None:
    assert GitPluginSource is not None
    assert LocalPluginSource is not None
    assert PluginInstalled is not None
    assert PluginRemoved is not None
    assert PluginRemoving is not None
    assert PluginSource is not None
    assert UrlPluginSource is not None


def test_submodule_imports() -> None:
    assert plugins_hook is not None
    assert LoadedPlugin is not None
    assert PluginManager is not None
    assert PluginManifest is not None
    assert create_plugin_tools_server is not None
