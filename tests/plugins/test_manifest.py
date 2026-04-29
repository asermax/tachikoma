"""Tests for plugin manifest parsing.

Covers AC-MP-1 through AC-MP-9 from the DLT-048 spec:
- AC-MP-1: Native TOML manifest round-trip
- AC-MP-2: CC manifest fallback with skills/ directory
- AC-MP-3: Ignored CC contributions logged and returned
- AC-MP-5: Native takes precedence over CC
- AC-MP-6: Neither manifest returns None
- AC-MP-7: Malformed TOML/JSON / missing required fields
- AC-MP-9: Zero skill directories is valid (no-op plugin)
- Path-traversal rejection (.. segments, absolute paths, out-of-tree)
"""

import json
from pathlib import Path

import pytest

from tachikoma.plugins.manifest import (
    CcManifest,
    PluginManifest,
    TachikomaManifest,
    _resolve_stays_within,
    log_ignored_cc_contributions,
    parse_manifest,
)


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory."""
    p = tmp_path / "my-plugin"
    p.mkdir()
    return p


def _write_native(
    plugin_dir: Path,
    *,
    name: str = "test-plugin",
    version: str | None = "1.0.0",
    description: str = "A test plugin",
    skills: list[str] | None = None,
) -> Path:
    """Write a ``tachikoma-plugin.toml`` into *plugin_dir*."""
    lines = [
        f'name = "{name}"',
        f'description = "{description}"',
    ]
    if version is not None:
        lines.append(f'version = "{version}"')
    if skills is not None:
        lines.append(f"skills = {skills!r}")
    toml_path = plugin_dir / "tachikoma-plugin.toml"
    toml_path.write_text("\n".join(lines) + "\n")
    return toml_path


def _write_cc(
    plugin_dir: Path,
    *,
    name: str = "cc-plugin",
    version: str | None = None,
    description: str | None = None,
    skills: str | None = None,
    extra: dict | None = None,
) -> Path:
    """Write a ``.claude-plugin/plugin.json`` into *plugin_dir*."""
    cc_dir = plugin_dir / ".claude-plugin"
    cc_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {"name": name}
    if version is not None:
        data["version"] = version
    if description is not None:
        data["description"] = description
    if skills is not None:
        data["skills"] = skills
    if extra is not None:
        data.update(extra)
    json_path = cc_dir / "plugin.json"
    json_path.write_text(json.dumps(data))
    return json_path


class TestTachikomaManifestModel:
    """Tests for the TachikomaManifest Pydantic model (AC-MP-1, AC-MP-7)."""

    def test_valid_minimal(self) -> None:
        """AC-MP-1: Name and description are sufficient."""
        m = TachikomaManifest(name="foo", description="bar")
        assert m.name == "foo"
        assert m.description == "bar"
        assert m.version is None
        assert m.skills == []

    def test_valid_full(self) -> None:
        """AC-MP-1: All fields populated."""
        m = TachikomaManifest(
            name="foo",
            description="bar",
            version="2.0",
            skills=["skills/a", "skills/b"],
        )
        assert m.version == "2.0"
        assert m.skills == ["skills/a", "skills/b"]

    def test_extra_fields_rejected(self) -> None:
        """AC-MP-7: Unknown fields cause validation error (extra='forbid')."""
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            TachikomaManifest(name="foo", description="bar", unknown="baz")

    def test_missing_description_rejected(self) -> None:
        """AC-MP-7: Description is required for native manifests."""
        with pytest.raises(Exception, match="description"):
            TachikomaManifest(name="foo")

    def test_invalid_name_rejected(self) -> None:
        """AC-MP-7 / AC-PSD-6: Name must match alias pattern."""
        with pytest.raises(Exception, match="manifest name must match"):
            TachikomaManifest(name="Bad Name!", description="x")

    def test_name_with_uppercase_rejected(self) -> None:
        """AC-PSD-6: Uppercase rejected."""
        with pytest.raises(Exception, match="manifest name must match"):
            TachikomaManifest(name="MyPlugin", description="x")

    def test_name_with_colon_rejected(self) -> None:
        """AC-PSD-6: Colon rejected."""
        with pytest.raises(Exception, match="manifest name must match"):
            TachikomaManifest(name="ns:plugin", description="x")

    def test_skills_default_empty(self) -> None:
        """AC-MP-9: Zero skill directories is valid."""
        m = TachikomaManifest(name="noop", description="no-op")
        assert m.skills == []


class TestCcManifestModel:
    """Tests for the CcManifest Pydantic model (AC-MP-2, AC-MP-7)."""

    def test_valid_minimal(self) -> None:
        """Only name is required for CC manifests."""
        m = CcManifest(name="cc-test")
        assert m.name == "cc-test"
        assert m.description is None
        assert m.skills is None

    def test_extra_fields_ignored(self) -> None:
        """CC manifests tolerate unknown fields (extra='ignore')."""
        m = CcManifest(
            name="cc-test",
            commands=[{"name": "foo"}],
            mcpServers={"bar": {"command": "baz"}},
        )
        assert m.name == "cc-test"

    def test_missing_name_rejected(self) -> None:
        """AC-MP-7: name is required."""
        with pytest.raises(Exception, match="name"):
            CcManifest(description="no name")

    def test_skills_path_field(self) -> None:
        """CC skills is a single optional path string."""
        m = CcManifest(name="cc-test", skills="custom-skills/")
        assert m.skills == "custom-skills/"


class TestPathTraversalProtection:
    """Tests for skill-path validation (path-traversal rejection)."""

    def test_empty_string_rejected(self) -> None:
        """Empty skill path rejected."""
        with pytest.raises(Exception, match="must not contain empty strings"):
            TachikomaManifest(name="p", description="d", skills=[""])

    def test_absolute_path_rejected(self) -> None:
        """Absolute skill path rejected."""
        with pytest.raises(Exception, match="absolute"):
            TachikomaManifest(name="p", description="d", skills=["/etc/passwd"])

    def test_dot_dot_segment_rejected(self) -> None:
        """Path with '..' segment rejected."""
        with pytest.raises(Exception, match=r"'\.\.' segments"):
            TachikomaManifest(name="p", description="d", skills=["../../../etc"])

    def test_clean_relative_path_passes(self) -> None:
        """Clean relative paths pass validation."""
        m = TachikomaManifest(name="p", description="d", skills=["skills/code"])
        assert m.skills == ["skills/code"]

    def test_cc_absolute_rejected(self) -> None:
        """CC manifest: absolute skills path rejected."""
        with pytest.raises(Exception, match="absolute"):
            CcManifest(name="p", skills="/absolute/path")

    def test_cc_dot_dot_rejected(self) -> None:
        """CC manifest: '..' segment rejected."""
        with pytest.raises(Exception, match=r"'\.\.' segments"):
            CcManifest(name="p", skills="../escape")

    def test_cc_empty_rejected(self) -> None:
        """CC manifest: empty skills string rejected."""
        with pytest.raises(Exception, match="must not be empty"):
            CcManifest(name="p", skills="")

    def test_out_of_tree_resolution_rejected(self, plugin_dir: Path) -> None:
        """Skill path resolving outside plugin dir is caught by parse_manifest."""
        _write_native(plugin_dir, skills=["../../escape"])
        with pytest.raises(Exception, match=r"'\.\.' segments"):
            parse_manifest(plugin_dir)

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """Resolved path outside plugin dir raises ValueError."""
        plugin = tmp_path / "plug"
        plugin.mkdir()
        _write_native(plugin, skills=["skills/ok"])
        # Simulate a relative path that resolves outside
        with pytest.raises(ValueError, match="resolves outside"):
            _resolve_stays_within(plugin, "../../escape")


class TestParseManifestNative:
    """Tests for parsing native Tachikoma manifests."""

    def test_round_trip_minimal(self, plugin_dir: Path) -> None:
        """AC-MP-1: Minimal native manifest round-trips."""
        _write_native(plugin_dir, skills=["skills/a"])
        (plugin_dir / "skills" / "a").mkdir(parents=True)

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.source_format == "tachikoma"
        assert result.name == "test-plugin"
        assert result.version == "1.0.0"
        assert result.description == "A test plugin"
        assert len(result.skill_dirs) == 1
        assert result.skill_dirs[0] == (plugin_dir / "skills" / "a").resolve()

    def test_round_trip_no_skills(self, plugin_dir: Path) -> None:
        """AC-MP-9: Native manifest with zero skills is valid."""
        _write_native(plugin_dir, skills=[])
        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.skill_dirs == []

    def test_round_trip_multiple_skills(self, plugin_dir: Path) -> None:
        """AC-MP-1: Multiple skill directories."""
        _write_native(plugin_dir, skills=["skills/code", "skills/docs"])
        (plugin_dir / "skills" / "code").mkdir(parents=True)
        (plugin_dir / "skills" / "docs").mkdir(parents=True)

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert len(result.skill_dirs) == 2

    def test_invalid_toml_raises(self, plugin_dir: Path) -> None:
        """AC-MP-7: Malformed TOML raises ValueError."""
        toml_path = plugin_dir / "tachikoma-plugin.toml"
        toml_path.write_text("this is not valid toml {{{")
        with pytest.raises(Exception, match="Failed to read"):
            parse_manifest(plugin_dir)

    def test_missing_required_field_raises(self, plugin_dir: Path) -> None:
        """AC-MP-7: Missing required field (description) raises."""
        toml_path = plugin_dir / "tachikoma-plugin.toml"
        toml_path.write_text('name = "foo"\n')
        with pytest.raises(Exception, match="description"):
            parse_manifest(plugin_dir)

    def test_no_version_ok(self, plugin_dir: Path) -> None:
        """Version is optional in native manifests."""
        _write_native(plugin_dir, version=None, skills=[])
        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.version is None


class TestParseManifestCc:
    """Tests for parsing Claude Code manifests."""

    def test_cc_with_skills_dir(self, plugin_dir: Path) -> None:
        """AC-MP-2: CC manifest uses conventional skills/ directory."""
        _write_cc(plugin_dir, description="CC plugin")
        (plugin_dir / "skills").mkdir()

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.source_format == "cc"
        assert result.name == "cc-plugin"
        assert result.description == "CC plugin"
        assert len(result.skill_dirs) == 1
        assert result.skill_dirs[0] == (plugin_dir / "skills").resolve()

    def test_cc_without_skills_dir(self, plugin_dir: Path) -> None:
        """AC-MP-4: CC plugin without skills/ directory — zero skills."""
        _write_cc(plugin_dir)
        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.skill_dirs == []

    def test_cc_with_explicit_skills_path(self, plugin_dir: Path) -> None:
        """CC manifest with explicit skills path overrides conventional dir."""
        _write_cc(plugin_dir, skills="custom/")
        (plugin_dir / "custom").mkdir()

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert len(result.skill_dirs) == 1
        assert result.skill_dirs[0] == (plugin_dir / "custom").resolve()

    def test_cc_invalid_json_raises(self, plugin_dir: Path) -> None:
        """AC-MP-7: Malformed JSON raises ValueError."""
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text("{invalid json")

        with pytest.raises(Exception, match="Failed to read"):
            parse_manifest(plugin_dir)

    def test_cc_missing_name_raises(self, plugin_dir: Path) -> None:
        """AC-MP-7: Missing required field 'name' raises."""
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text('{"description": "no name"}')

        with pytest.raises(Exception, match="name"):
            parse_manifest(plugin_dir)

    def test_cc_json_array_raises(self, plugin_dir: Path) -> None:
        """AC-MP-7: JSON array instead of object raises."""
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text("[]")

        with pytest.raises(Exception, match="JSON object"):
            parse_manifest(plugin_dir)


class TestNativeTakesPrecedence:
    """AC-MP-5: When both manifests exist, native wins."""

    def test_both_manifests_native_wins(self, plugin_dir: Path) -> None:
        """AC-MP-5: Native manifest data used; CC contributions not logged."""
        _write_native(plugin_dir, name="native-name", skills=[])
        _write_cc(
            plugin_dir,
            name="cc-name",
            extra={"commands": [{"name": "cmd"}]},
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.source_format == "tachikoma"
        assert result.name == "native-name"
        # CC contributions are NOT accumulated when native takes precedence
        assert result.ignored_cc_contributions == []


class TestNeitherManifest:
    """AC-MP-6: Neither manifest returns None."""

    def test_empty_dir_returns_none(self, plugin_dir: Path) -> None:
        """AC-MP-6: No manifest files → None."""
        result = parse_manifest(plugin_dir)
        assert result is None

    def test_unrelated_files_returns_none(self, plugin_dir: Path) -> None:
        """AC-MP-6: Random files in dir → None."""
        (plugin_dir / "README.md").write_text("hello")
        result = parse_manifest(plugin_dir)
        assert result is None


class TestLogIgnoredCcContributions:
    """AC-MP-3: Ignored CC contributions logged and returned."""

    def test_single_contribution(self, plugin_dir: Path) -> None:
        """AC-MP-3: Single ignored contribution type returned."""
        _write_cc(
            plugin_dir,
            extra={"commands": [{"name": "cmd"}]},
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert "commands" in result.ignored_cc_contributions

        logged = log_ignored_cc_contributions("test-alias", result)
        assert logged == ["commands"]

    def test_multiple_contributions(self, plugin_dir: Path) -> None:
        """AC-MP-3: Multiple ignored contribution types returned."""
        _write_cc(
            plugin_dir,
            extra={
                "commands": [{"name": "cmd"}],
                "mcpServers": {"srv": {"command": "n"}},
                "agents": [{"name": "agent1"}],
            },
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        # Check that all three are present (order matches _CC_CONTRIBUTION_TYPES)
        assert set(result.ignored_cc_contributions) == {
            "commands",
            "mcpServers",
            "agents",
        }

        logged = log_ignored_cc_contributions("multi", result)
        assert set(logged) == {"commands", "mcpServers", "agents"}

    def test_native_manifest_returns_empty_list(self, plugin_dir: Path) -> None:
        """AC-MP-3: Native manifest produces no ignored contributions."""
        _write_native(plugin_dir, skills=[])
        # Also add CC manifest — but native wins, so no CC contributions logged
        _write_cc(plugin_dir, extra={"commands": [{"name": "cmd"}]})

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.ignored_cc_contributions == []

        logged = log_ignored_cc_contributions("native-wins", result)
        assert logged == []

    def test_empty_contribution_value_not_listed(self, plugin_dir: Path) -> None:
        """Contribution key present but falsy/empty is not listed."""
        _write_cc(plugin_dir, extra={"commands": []})

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert "commands" not in result.ignored_cc_contributions

    def test_all_contribution_types(self, plugin_dir: Path) -> None:
        """AC-MP-3: All known contribution types are recognized."""
        extra = {
            k: [{"name": "x"}] if isinstance(k, str) else k
            for k in (
                "commands",
                "mcpServers",
                "agents",
                "hooks",
                "lspServers",
                "monitors",
                "themes",
                "outputStyles",
            )
        }
        # mcpServers is a dict, not list
        extra["mcpServers"] = {"srv": {"command": "n"}}
        _write_cc(plugin_dir, extra=extra)

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert len(result.ignored_cc_contributions) == 8


class TestPluginManifest:
    """Tests for the PluginManifest frozen dataclass."""

    def test_frozen(self) -> None:
        """PluginManifest is immutable."""
        pm = PluginManifest(
            name="p",
            version="1.0",
            description="d",
            source_format="tachikoma",
            skill_dirs=[],
        )
        with pytest.raises(AttributeError):
            pm.name = "other"  # type: ignore[misc]

    def test_default_ignored_cc(self) -> None:
        """ignored_cc_contributions defaults to empty list."""
        pm = PluginManifest(
            name="p",
            version=None,
            description=None,
            source_format="cc",
            skill_dirs=[],
        )
        assert pm.ignored_cc_contributions == []
