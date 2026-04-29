"""Tests for plugin source models and alias validation.

Tests for DLT-048 Batch 1: Plugin configuration models.
Covers AC-PSD-3, AC-PSD-4, AC-PSD-5, AC-PSD-6, AC-PSD-10,
AC-PSD-11, AC-PSD-12, AC-PSD-13.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tachikoma.plugins.sources import (
    ALIAS_PATTERN,
    GitPluginSource,
    LocalPluginSource,
    UrlPluginSource,
    parse_plugin_source,
    validate_alias,
)


class TestGitPluginSource:
    """Tests for GitPluginSource model."""

    def test_valid_git_source(self) -> None:
        """A git source with all required fields validates."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")

        assert source.git == "https://github.com/owner/repo.git"
        assert source.ref == "v1.0.0"
        assert source.subdir is None

    def test_valid_git_source_with_subdir(self) -> None:
        """A git source with optional subdir validates."""
        source = GitPluginSource(
            git="https://github.com/owner/repo.git",
            subdir="plugin",
            ref="main",
        )

        assert source.subdir == "plugin"

    def test_gh_shorthand_expanded(self) -> None:
        """AC-PSD-10: gh:owner/repo is expanded to https://github.com/owner/repo.git."""
        source = GitPluginSource(git="gh:owner/repo", ref="v1.0.0")

        assert source.git == "https://github.com/owner/repo.git"

    def test_github_shorthand_expanded(self) -> None:
        """AC-PSD-10: github:owner/repo is expanded to https://github.com/owner/repo.git."""
        source = GitPluginSource(git="github:owner/repo", ref="v1.0.0")

        assert source.git == "https://github.com/owner/repo.git"

    def test_full_url_preserved(self) -> None:
        """AC-PSD-10: A full HTTPS URL is preserved unchanged."""
        source = GitPluginSource(
            git="https://gitlab.com/owner/repo.git", ref="v1.0.0"
        )

        assert source.git == "https://gitlab.com/owner/repo.git"

    def test_sha_shaped_ref_rejected(self) -> None:
        """AC-PSD-11: A ref that looks like a commit SHA (7-40 hex chars) is rejected."""
        with pytest.raises(ValidationError, match="commit SHA"):
            GitPluginSource(git="https://github.com/owner/repo.git", ref="abcdef0")

    def test_40_char_sha_rejected(self) -> None:
        """AC-PSD-11: A full 40-character SHA is rejected."""
        sha = "a" * 40
        with pytest.raises(ValidationError, match="commit SHA"):
            GitPluginSource(git="https://github.com/owner/repo.git", ref=sha)

    def test_short_branch_name_with_hex_chars_accepted(self) -> None:
        """A 6-char hex-looking ref is accepted (below 7-char threshold)."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="abcde")

        assert source.ref == "abcde"

    def test_branch_name_with_non_hex_chars_accepted(self) -> None:
        """A branch name that is longer than 7 chars but non-hex is accepted."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="feature/my-branch")

        assert source.ref == "feature/my-branch"

    def test_missing_ref_rejected(self) -> None:
        """AC-PSD-4: A git source without a ref field fails validation."""
        with pytest.raises(ValidationError, match="ref"):
            GitPluginSource(git="https://github.com/owner/repo.git")

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected due to extra='forbid'."""
        with pytest.raises(ValidationError):
            GitPluginSource(
                git="https://github.com/owner/repo.git",
                ref="v1.0.0",
                url="https://example.com/plugin.tar.gz",
            )


class TestUrlPluginSource:
    """Tests for UrlPluginSource model."""

    def test_valid_tar_gz_url(self) -> None:
        """A valid HTTPS .tar.gz URL validates."""
        source = UrlPluginSource(url="https://example.com/plugin.tar.gz")

        assert source.url == "https://example.com/plugin.tar.gz"
        assert source.subdir is None

    def test_valid_tgz_url(self) -> None:
        """A valid HTTPS .tgz URL validates."""
        source = UrlPluginSource(url="https://example.com/plugin.tgz")

        assert source.url == "https://example.com/plugin.tgz"

    def test_valid_zip_url(self) -> None:
        """A valid HTTPS .zip URL validates."""
        source = UrlPluginSource(url="https://example.com/plugin.zip")

        assert source.url == "https://example.com/plugin.zip"

    def test_valid_url_with_subdir(self) -> None:
        """A valid URL with optional subdir validates."""
        source = UrlPluginSource(
            url="https://example.com/plugin.tar.gz",
            subdir="inner",
        )

        assert source.subdir == "inner"

    def test_http_scheme_rejected(self) -> None:
        """AC-PSD-12: Non-HTTPS scheme is rejected."""
        with pytest.raises(ValidationError, match="HTTPS"):
            UrlPluginSource(url="http://example.com/plugin.tar.gz")

    def test_unrecognized_extension_rejected(self) -> None:
        """AC-PSD-13: A URL with a non-archive extension is rejected."""
        with pytest.raises(ValidationError, match="\\.tar\\.gz.*\\.tgz.*\\.zip"):
            UrlPluginSource(url="https://example.com/plugin.exe")

    def test_no_extension_rejected(self) -> None:
        """AC-PSD-13: A URL with no archive extension is rejected."""
        with pytest.raises(ValidationError, match="\\.tar\\.gz.*\\.tgz.*\\.zip"):
            UrlPluginSource(url="https://example.com/plugin")

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected due to extra='forbid'."""
        with pytest.raises(ValidationError):
            UrlPluginSource(
                url="https://example.com/plugin.tar.gz",
                git="https://github.com/owner/repo.git",
            )


class TestLocalPluginSource:
    """Tests for LocalPluginSource model."""

    def test_valid_absolute_path(self) -> None:
        """AC-PSD-2: An absolute path validates."""
        source = LocalPluginSource(path=Path("/home/user/dev/my-plugin"))

        assert source.path == Path("/home/user/dev/my-plugin")

    def test_relative_path_rejected(self) -> None:
        """AC-PSD-5: A relative path is rejected."""
        with pytest.raises(ValidationError, match="absolute"):
            LocalPluginSource(path=Path("my-plugin"))

    def test_relative_path_with_parent_rejected(self) -> None:
        """AC-PSD-5: A path like ../something is rejected."""
        with pytest.raises(ValidationError, match="absolute"):
            LocalPluginSource(path=Path("../my-plugin"))

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected due to extra='forbid'."""
        with pytest.raises(ValidationError):
            LocalPluginSource(
                path=Path("/home/user/plugin"),
                git="https://github.com/owner/repo.git",
            )

    def test_string_path_accepted(self) -> None:
        """A string path is coerced to Path."""
        source = LocalPluginSource(path="/home/user/dev/my-plugin")

        assert isinstance(source.path, Path)
        assert source.path.is_absolute()


class TestParsePluginSource:
    """Tests for the parse_plugin_source dispatch helper."""

    def test_git_variant_dispatched(self) -> None:
        """A dict with 'git' key dispatches to GitPluginSource."""
        source = parse_plugin_source({
            "git": "https://github.com/owner/repo.git",
            "ref": "v1.0.0",
        })

        assert isinstance(source, GitPluginSource)

    def test_url_variant_dispatched(self) -> None:
        """A dict with 'url' key dispatches to UrlPluginSource."""
        source = parse_plugin_source({"url": "https://example.com/plugin.tar.gz"})

        assert isinstance(source, UrlPluginSource)

    def test_local_variant_dispatched(self) -> None:
        """A dict with 'path' key dispatches to LocalPluginSource."""
        source = parse_plugin_source({"path": "/home/user/plugin"})

        assert isinstance(source, LocalPluginSource)

    def test_multiple_sources_rejected(self) -> None:
        """AC-PSD-3: More than one discriminator field is rejected."""
        with pytest.raises(ValueError, match="mutually exclusive"):
            parse_plugin_source({
                "git": "https://github.com/owner/repo.git",
                "url": "https://example.com/plugin.tar.gz",
                "ref": "v1.0.0",
            })

    def test_no_source_rejected(self) -> None:
        """AC-PSD-3: No discriminator field is rejected."""
        with pytest.raises(ValueError, match="none were provided"):
            parse_plugin_source({})

    def test_variant_validation_errors_propagate(self) -> None:
        """AC-PSD-4: Missing required fields in the variant propagate as errors."""
        with pytest.raises(ValidationError, match="ref"):
            parse_plugin_source({"git": "https://github.com/owner/repo.git"})

    def test_url_validation_errors_propagate(self) -> None:
        """UrlPluginSource validation errors propagate through dispatch."""
        with pytest.raises(ValidationError, match="HTTPS"):
            parse_plugin_source({"url": "http://example.com/plugin.tar.gz"})


class TestAliasValidation:
    """Tests for alias regex and validate_alias helper."""

    def test_valid_alphanumeric_alias(self) -> None:
        """A simple alphanumeric alias passes."""
        assert validate_alias("abc123") == "abc123"

    def test_valid_alias_with_hyphens(self) -> None:
        """An alias with hyphens passes."""
        assert validate_alias("code-review") == "code-review"

    def test_valid_single_char_alias(self) -> None:
        """A single alphanumeric char passes."""
        assert validate_alias("a") == "a"

    def test_valid_digit_start(self) -> None:
        """An alias starting with a digit passes."""
        assert validate_alias("0plugin") == "0plugin"

    def test_rejects_uppercase(self) -> None:
        """AC-PSD-6: Uppercase letters are rejected."""
        with pytest.raises(ValueError, match="Invalid plugin alias"):
            validate_alias("CodeReview")

    def test_rejects_colon(self) -> None:
        """AC-PSD-6: Colons are rejected."""
        with pytest.raises(ValueError, match="Invalid plugin alias"):
            validate_alias("code:review")

    def test_rejects_slash(self) -> None:
        """AC-PSD-6: Slashes are rejected."""
        with pytest.raises(ValueError, match="Invalid plugin alias"):
            validate_alias("code/review")

    def test_rejects_empty_string(self) -> None:
        """An empty alias is rejected."""
        with pytest.raises(ValueError, match="Invalid plugin alias"):
            validate_alias("")

    def test_rejects_leading_hyphen(self) -> None:
        """A leading hyphen is rejected."""
        with pytest.raises(ValueError, match="Invalid plugin alias"):
            validate_alias("-plugin")

    def test_alias_pattern_regex(self) -> None:
        """ALIAS_PATTERN matches expected patterns."""
        assert ALIAS_PATTERN.match("code-review")
        assert ALIAS_PATTERN.match("a1")
        assert ALIAS_PATTERN.match("my-plugin-v2")
        assert not ALIAS_PATTERN.match("Code-Review")
        assert not ALIAS_PATTERN.match("code:review")
        assert not ALIAS_PATTERN.match("")
        assert not ALIAS_PATTERN.match("-plugin")
