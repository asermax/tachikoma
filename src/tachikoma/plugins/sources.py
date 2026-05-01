"""Plugin source models and alias validation.

Defines three frozen Pydantic models for plugin source variants (git, URL, local),
a discriminated-union type alias, a parsing helper enforcing exactly-one-source,
and alias regex validation used by both config loading and install operations.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
"""Regex pattern for valid plugin aliases.

Lowercase alphanumeric and hyphens; must start with alphanumeric.
"""

_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
"""Regex pattern for SHA-shaped refs (7-40 hex characters)."""

_GH_PREFIX = "gh:"
_GITHUB_PREFIX = "github:"

_RECOGNIZED_ARCHIVE_EXTENSIONS = (".tar.gz", ".tgz", ".zip")


def validate_alias(alias: str) -> str:
    """Validate a plugin alias against the required pattern.

    Args:
        alias: The alias string to validate.

    Returns:
        The alias string unchanged if valid.

    Raises:
        ValueError: If the alias does not match ``[a-z0-9][a-z0-9-]*``.
    """
    if not ALIAS_PATTERN.match(alias):
        raise ValueError(
            f"Invalid plugin alias '{alias}': must match [a-z0-9][a-z0-9-]*"
        )
    return alias


class GitPluginSource(BaseModel):
    """A git repository plugin source.

    The ``git`` field accepts ``gh:owner/repo`` and ``github:owner/repo``
    shorthand which is expanded to ``https://github.com/owner/repo.git``.
    The ``ref`` field must be a branch or tag name; bare commit SHAs are
    rejected at validation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    git: str = Field(description="Git repository URL (or gh:/github: shorthand)")
    subdir: str | None = Field(
        default=None,
        description="Optional subdirectory within the repository",
    )
    ref: str = Field(description="Branch or tag name to check out")

    @field_validator("git", mode="before")
    @classmethod
    def expand_github_shorthand(cls, v: object) -> object:
        """Expand gh: and github: shorthand to full HTTPS URLs."""
        if not isinstance(v, str):
            return v

        if v.startswith(_GH_PREFIX):
            remainder = v[len(_GH_PREFIX):]
            return f"https://github.com/{remainder}.git"

        if v.startswith(_GITHUB_PREFIX):
            remainder = v[len(_GITHUB_PREFIX):]
            return f"https://github.com/{remainder}.git"

        return v

    @field_validator("ref", mode="after")
    @classmethod
    def reject_sha_shaped_refs(cls, v: str) -> str:
        """Reject refs that look like commit SHAs (7-40 hex characters)."""
        if _SHA_PATTERN.match(v):
            raise ValueError(
                f"ref must be a branch or tag name, not a commit SHA (got '{v}')"
            )
        return v


class UrlPluginSource(BaseModel):
    """An HTTPS archive URL plugin source.

    Only ``https://`` URLs are accepted, and the URL must end with one of
    the recognized archive extensions: ``.tar.gz``, ``.tgz``, ``.zip``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(description="HTTPS URL pointing to a .tar.gz, .tgz, or .zip archive")
    subdir: str | None = Field(
        default=None,
        description="Optional subdirectory within the extracted archive",
    )

    @field_validator("url", mode="after")
    @classmethod
    def enforce_https_and_extension(cls, v: str) -> str:
        """Enforce HTTPS scheme and recognized archive extension."""
        if not v.startswith("https://"):
            raise ValueError(
                f"Only HTTPS URLs are supported for plugin sources (got '{v}')"
            )

        lower = v.lower()
        if not any(lower.endswith(ext) for ext in _RECOGNIZED_ARCHIVE_EXTENSIONS):
            extensions = ", ".join(_RECOGNIZED_ARCHIVE_EXTENSIONS)
            raise ValueError(
                f"URL must end with one of {extensions} (got '{v}')"
            )

        return v


class LocalPluginSource(BaseModel):
    """A local filesystem path plugin source.

    The path must be absolute. Relative paths are rejected at validation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path = Field(description="Absolute path to the plugin directory")

    @field_validator("path", mode="after")
    @classmethod
    def reject_relative_paths(cls, v: Path) -> Path:
        """Reject relative paths; only absolute paths are allowed."""
        if not v.is_absolute():
            raise ValueError(
                f"Plugin path must be absolute, got relative path '{v}'"
            )
        return v


PluginSource = GitPluginSource | UrlPluginSource | LocalPluginSource
"""Type alias for the three plugin source variants."""


def parse_plugin_source(data: dict) -> PluginSource:
    """Parse a raw dict into the correct PluginSource variant.

    Enforces the "exactly one of git/url/path" rule by counting populated
    discriminator fields. Dispatches to the matching variant's
    ``model_validate`` so that per-variant field validators run.

    Args:
        data: A raw dictionary from TOML config or similar input.

    Returns:
        The validated PluginSource instance.

    Raises:
        ValueError: If zero or more than one discriminator fields are present,
            or if the variant's own validation fails.
    """
    populated_keys = [k for k in ("git", "url", "path") if k in data and data[k] is not None]

    if len(populated_keys) == 0:
        raise ValueError(
            "Plugin source must specify exactly one of 'git', 'url', or 'path'; "
            "none were provided"
        )

    if len(populated_keys) > 1:
        names = ", ".join(f"'{k}'" for k in populated_keys)
        raise ValueError(
            f"Plugin source fields are mutually exclusive; found {names}"
        )

    key = populated_keys[0]

    if key == "git":
        return GitPluginSource.model_validate(data)
    elif key == "url":
        return UrlPluginSource.model_validate(data)
    else:
        return LocalPluginSource.model_validate(data)
