"""Plugin configuration schema model and validation engine.

Declares the typed config schema that plugin authors put in their manifests,
validates user-supplied values against those schemas, and produces diagnostics
on failure.

Public API:
- ``ConfigFieldSchema`` — Pydantic model for a single declared setting.
- ``ConfigDiagnostic``   — Human-readable validation finding.
- ``ConfigValidationResult`` — Outcome of ``validate_config()``.
- ``validate_config()`` — Core validation function (never raises).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Type-checking helpers
# ---------------------------------------------------------------------------

_TYPE_CHECKERS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "boolean": (bool,),
    "float": (int, float),
}

_log = logger.bind(component="plugins")


def _type_name(value: Any) -> str:
    return type(value).__name__


# ---------------------------------------------------------------------------
# ConfigFieldSchema
# ---------------------------------------------------------------------------


class ConfigFieldSchema(BaseModel):
    """Schema for a single plugin config field declared in the manifest.

    ``type`` must be one of the four supported literals. ``required`` and
    ``default`` are mutually exclusive — validated by a ``model_validator``
    that runs after field-level parsing so both values are available.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["string", "integer", "boolean", "float"]
    description: str
    default: str | int | bool | float | None = None
    required: bool = False

    @model_validator(mode="after")
    def _validate_field_constraints(self) -> ConfigFieldSchema:
        if self.required and self.default is not None:
            raise ValueError(
                "Config field declares both required=true and a default value. "
                "Required fields cannot have defaults."
            )
        if self.default is not None:
            expected_types = _TYPE_CHECKERS[self.type]
            if not isinstance(self.default, expected_types):
                raise ValueError(
                    f"Default value has type {_type_name(self.default)}, "
                    f"expected {self.type}."
                )
            # bool is a subclass of int in Python — reject bool defaults for
            # integer/float fields, and reject int defaults for boolean fields.
            if self.type == "integer" and isinstance(self.default, bool):
                raise ValueError(
                    "Default value has type bool, expected integer."
                )
            if self.type == "float" and isinstance(self.default, bool):
                raise ValueError(
                    "Default value has type bool, expected float."
                )
        return self


# ---------------------------------------------------------------------------
# Diagnostic types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigDiagnostic:
    """A single validation finding for a config field."""

    field: str | None
    message: str


@dataclass(frozen=True)
class ConfigValidationResult:
    """Outcome of config validation — always returned, never raised.

    ``is_valid`` is ``True`` when ``diagnostics`` is empty. ``values``
    contains the validated config dict for all passing fields (empty when
    invalid). ``unknown_keys`` lists user-supplied keys not in the schema
    (logged as warnings, not errors).
    """

    values: dict[str, str | int | bool | float]
    diagnostics: list[ConfigDiagnostic]
    unknown_keys: list[str]

    @property
    def is_valid(self) -> bool:
        return len(self.diagnostics) == 0


# ---------------------------------------------------------------------------
# Public validation function
# ---------------------------------------------------------------------------


def validate_config(
    schema: dict[str, ConfigFieldSchema],
    user_values: dict[str, Any],
) -> ConfigValidationResult:
    """Validate *user_values* against *schema*.

    Returns a ``ConfigValidationResult`` — never raises. Unknown user keys
    are collected as warnings (not errors).
    """
    diagnostics: list[ConfigDiagnostic] = []
    values: dict[str, str | int | bool | float] = {}
    unknown_keys: list[str] = []

    for field_name, field_schema in schema.items():
        if field_name in user_values:
            raw = user_values[field_name]
            coerced = _coerce_if_allowed(raw, field_schema.type)
            if coerced is _FAIL:
                diagnostics.append(
                    ConfigDiagnostic(
                        field=field_name,
                        message=(
                            f"Config field '{field_name}' expects type "
                            f"{field_schema.type}, got {_type_name(raw)}."
                        ),
                    )
                )
            else:
                values[field_name] = coerced
        elif field_schema.required:
            diagnostics.append(
                ConfigDiagnostic(
                    field=field_name,
                    message=f"Required config field '{field_name}' is missing.",
                )
            )
        elif field_schema.default is not None:
            values[field_name] = field_schema.default

    # Optional fields without a default and without a user value are simply
    # absent from ``values`` — they are NOT populated with None.

    for key in user_values:
        if key not in schema:
            unknown_keys.append(key)
            _log.warning(
                "Unknown config key '{}' not in plugin schema — ignored.",
                key,
            )

    return ConfigValidationResult(
        values=values,
        diagnostics=diagnostics,
        unknown_keys=unknown_keys,
    )


# Sentinel for coercion failure (distinct from any valid value).
_FAIL = object()


def _coerce_if_allowed(value: Any, expected_type: str) -> Any:
    """Type-check *value* against *expected_type* with float→int coercion."""
    if expected_type == "string":
        return value if isinstance(value, str) else _FAIL

    if expected_type == "boolean":
        return value if isinstance(value, bool) else _FAIL

    if expected_type == "integer":
        if isinstance(value, bool):
            return _FAIL
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return _FAIL

    if expected_type == "float":
        if isinstance(value, bool):
            return _FAIL
        if isinstance(value, (int, float)):
            return float(value)
        return _FAIL

    return _FAIL
