"""Tests for plugin config schema model and validation engine.

See: docs/delta-specs/DLT-172.md (R1, R3 acceptance criteria)
See: docs/design/DES-001-testing-conventions.md
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tachikoma.plugins.config_schema import (
    ConfigDiagnostic,
    ConfigFieldSchema,
    ConfigValidationResult,
    validate_config,
)

# ---------------------------------------------------------------------------
# ConfigFieldSchema
# ---------------------------------------------------------------------------


class TestConfigFieldSchema:
    """Tests for ConfigFieldSchema Pydantic model."""

    def test_valid_minimal_field(self) -> None:
        """AC: A field with only type and description is valid."""
        field = ConfigFieldSchema(type="string", description="A setting")
        assert field.type == "string"
        assert field.description == "A setting"
        assert field.default is None
        assert field.required is False

    def test_valid_full_field(self) -> None:
        """AC: A field with all optional attributes is valid."""
        field = ConfigFieldSchema(
            type="integer",
            description="Timeout",
            default=30,
            required=False,
        )
        assert field.default == 30
        assert field.required is False

    def test_all_four_types(self) -> None:
        """AC: All four basic types are accepted."""
        for t in ("string", "integer", "boolean", "float"):
            field = ConfigFieldSchema(type=t, description=f"A {t} field")
            assert field.type == t

    def test_unsupported_type_rejected(self) -> None:
        """AC: Unsupported type values cause validation error.

        See: R1 — manifest with unsupported type fails with diagnostic.
        """
        with pytest.raises(ValidationError, match="literal"):
            ConfigFieldSchema(type="array", description="Items")

    def test_required_with_default_rejected(self) -> None:
        """AC: required=true and default present is a conflict.

        See: R1 — required fields cannot have defaults.
        """
        with pytest.raises(ValidationError, match="required=true"):
            ConfigFieldSchema(
                type="string",
                description="Key",
                required=True,
                default="fallback",
            )

    def test_default_type_mismatch_string_for_integer(self) -> None:
        """AC: Default value type must match declared field type.

        See: R1 — default type mismatch produces diagnostic.
        """
        with pytest.raises(ValidationError, match="type str"):
            ConfigFieldSchema(
                type="integer",
                description="Timeout",
                default="thirty",
            )

    def test_default_type_mismatch_bool_for_integer(self) -> None:
        """AC: Boolean default for integer field is rejected (bool is int subclass)."""
        with pytest.raises(ValidationError, match="bool"):
            ConfigFieldSchema(
                type="integer",
                description="Flag",
                default=True,
            )

    def test_default_type_mismatch_bool_for_float(self) -> None:
        """AC: Boolean default for float field is rejected."""
        with pytest.raises(ValidationError, match="bool"):
            ConfigFieldSchema(
                type="float",
                description="Rate",
                default=False,
            )

    def test_missing_description_rejected(self) -> None:
        """AC: description is required for all config fields.

        See: R1 — missing description causes parse failure.
        """
        with pytest.raises(ValidationError):
            ConfigFieldSchema(type="string")

    def test_extra_fields_rejected(self) -> None:
        """AC: extra='forbid' rejects unknown keys."""
        with pytest.raises(ValidationError):
            ConfigFieldSchema(
                type="string",
                description="Key",
                unknown_field="oops",
            )

    def test_frozen_model(self) -> None:
        """AC: Model is immutable (frozen=True)."""
        field = ConfigFieldSchema(type="string", description="A setting")
        with pytest.raises(ValidationError):
            field.description = "modified"

    def test_valid_defaults_for_each_type(self) -> None:
        """AC: Defaults of the correct type are accepted for all types."""
        cases = [
            ("string", "hello"),
            ("integer", 42),
            ("boolean", True),
            ("boolean", False),
            ("float", 3.14),
            ("float", 2),  # int is valid for float
        ]
        for field_type, default_val in cases:
            field = ConfigFieldSchema(
                type=field_type,
                description=f"A {field_type} field",
                default=default_val,
            )
            assert field.default == default_val


# ---------------------------------------------------------------------------
# ConfigDiagnostic
# ---------------------------------------------------------------------------


class TestConfigDiagnostic:
    """Tests for ConfigDiagnostic dataclass."""

    def test_field_diagnostic(self) -> None:
        d = ConfigDiagnostic(field="api_key", message="is missing")
        assert d.field == "api_key"
        assert d.message == "is missing"

    def test_non_field_diagnostic(self) -> None:
        d = ConfigDiagnostic(field=None, message="general error")
        assert d.field is None

    def test_frozen(self) -> None:
        d = ConfigDiagnostic(field="x", message="y")
        with pytest.raises(AttributeError):
            d.field = "z"


# ---------------------------------------------------------------------------
# ConfigValidationResult
# ---------------------------------------------------------------------------


class TestConfigValidationResult:
    """Tests for ConfigValidationResult dataclass."""

    def test_is_valid_when_no_diagnostics(self) -> None:
        result = ConfigValidationResult(
            values={"k": "v"},
            diagnostics=[],
            unknown_keys=[],
        )
        assert result.is_valid

    def test_is_invalid_with_diagnostics(self) -> None:
        result = ConfigValidationResult(
            values={},
            diagnostics=[ConfigDiagnostic(field="x", message="bad")],
            unknown_keys=[],
        )
        assert not result.is_valid

    def test_frozen(self) -> None:
        result = ConfigValidationResult(
            values={}, diagnostics=[], unknown_keys=[]
        )
        with pytest.raises(AttributeError):
            result.values = {"x": 1}



class TestValidateConfig:
    """Tests for validate_config() covering R3 acceptance criteria."""

    def test_happy_path_all_types(self) -> None:
        """AC: Valid values for all four types pass validation."""
        schema = {
            "name": ConfigFieldSchema(type="string", description="Name"),
            "count": ConfigFieldSchema(type="integer", description="Count"),
            "enabled": ConfigFieldSchema(type="boolean", description="On/off"),
            "rate": ConfigFieldSchema(type="float", description="Rate"),
        }
        user = {"name": "test", "count": 5, "enabled": True, "rate": 1.5}
        result = validate_config(schema, user)

        assert result.is_valid
        assert result.values == {"name": "test", "count": 5, "enabled": True, "rate": 1.5}
        assert result.unknown_keys == []

    def test_required_field_missing(self) -> None:
        """AC: Missing required field produces diagnostic, plugin fails.

        See: R3 — required string field with no user value → failed.
        """
        schema = {
            "api_key": ConfigFieldSchema(
                type="string", description="API key", required=True
            ),
        }
        result = validate_config(schema, {})

        assert not result.is_valid
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].field == "api_key"
        assert "missing" in result.diagnostics[0].message

    def test_type_mismatch_string_for_integer(self) -> None:
        """AC: Type mismatch on user value produces diagnostic.

        See: R3 — integer field with string value → type mismatch.
        """
        schema = {
            "timeout": ConfigFieldSchema(type="integer", description="Timeout"),
        }
        result = validate_config(schema, {"timeout": "not a number"})

        assert not result.is_valid
        assert result.diagnostics[0].field == "timeout"
        assert "integer" in result.diagnostics[0].message
        assert "str" in result.diagnostics[0].message

    def test_type_mismatch_int_for_boolean(self) -> None:
        """AC: Boolean field with int value fails (bool is int subclass).

        See: R3 — boolean field with 42 → type mismatch.
        """
        schema = {
            "debug": ConfigFieldSchema(type="boolean", description="Debug"),
        }
        result = validate_config(schema, {"debug": 42})

        assert not result.is_valid
        assert result.diagnostics[0].field == "debug"

    def test_type_mismatch_string_for_boolean_no_coercion(self) -> None:
        """AC: String 'true' is NOT coerced to boolean.

        See: R3 — string "true" for boolean → type mismatch.
        """
        schema = {
            "debug": ConfigFieldSchema(type="boolean", description="Debug"),
        }
        result = validate_config(schema, {"debug": "true"})

        assert not result.is_valid
        assert "str" in result.diagnostics[0].message

    def test_float_to_integer_coercion_zero_fraction(self) -> None:
        """AC: Float with zero fraction coerced to integer.

        See: R3 — 30.0 for integer field → coerced to 30.
        """
        schema = {
            "timeout": ConfigFieldSchema(type="integer", description="Timeout"),
        }
        result = validate_config(schema, {"timeout": 30.0})

        assert result.is_valid
        assert result.values["timeout"] == 30
        assert isinstance(result.values["timeout"], int)

    def test_float_to_integer_rejection_nonzero_fraction(self) -> None:
        """AC: Float with non-zero fraction rejected for integer field.

        See: R3 — 30.5 for integer field → type mismatch.
        """
        schema = {
            "timeout": ConfigFieldSchema(type="integer", description="Timeout"),
        }
        result = validate_config(schema, {"timeout": 30.5})

        assert not result.is_valid
        assert "integer" in result.diagnostics[0].message

    def test_defaults_applied_when_no_user_value(self) -> None:
        """AC: Default used when user provides no value.

        See: R3 — field with default and no user override → default used.
        """
        schema = {
            "timeout": ConfigFieldSchema(
                type="integer", description="Timeout", default=30
            ),
        }
        result = validate_config(schema, {})

        assert result.is_valid
        assert result.values == {"timeout": 30}

    def test_optional_no_default_absent_from_result(self) -> None:
        """AC: Optional field without default is absent from config dict.

        See: R3 — optional, no default, no user value → key absent.
        """
        schema = {
            "region": ConfigFieldSchema(type="string", description="Region"),
        }
        result = validate_config(schema, {})

        assert result.is_valid
        assert "region" not in result.values

    def test_empty_string_is_valid_value(self) -> None:
        """AC: Empty string is a valid present value, distinct from unset.

        See: R3 — empty string for optional string field is valid.
        """
        schema = {
            "api_key": ConfigFieldSchema(type="string", description="API key"),
        }
        result = validate_config(schema, {"api_key": ""})

        assert result.is_valid
        assert result.values == {"api_key": ""}

    def test_empty_string_not_treated_as_missing_for_required(self) -> None:
        """AC: Empty string satisfies required field (key is present)."""
        schema = {
            "api_key": ConfigFieldSchema(
                type="string", description="API key", required=True
            ),
        }
        result = validate_config(schema, {"api_key": ""})

        assert result.is_valid
        assert result.values == {"api_key": ""}

    def test_unknown_keys_not_errors(self) -> None:
        """AC: Unknown user keys are warnings, not errors.

        See: R3 — unknown key → plugin loaded, WARNING logged.
        """
        schema = {
            "api_key": ConfigFieldSchema(
                type="string", description="API key", required=True
            ),
        }
        result = validate_config(
            schema,
            {"api_key": "sk-...", "unknown_key": "value"},
        )

        assert result.is_valid
        assert result.values == {"api_key": "sk-..."}
        assert result.unknown_keys == ["unknown_key"]

    def test_empty_schema_empty_values(self) -> None:
        """AC: Empty schema and empty values → valid empty result."""
        result = validate_config({}, {})

        assert result.is_valid
        assert result.values == {}
        assert result.diagnostics == []
        assert result.unknown_keys == []

    def test_empty_schema_with_unknown_keys(self) -> None:
        """AC: Empty schema with user keys → valid with warnings."""
        result = validate_config({}, {"extra": True})

        assert result.is_valid
        assert result.values == {}
        assert result.unknown_keys == ["extra"]

    def test_user_value_overrides_default(self) -> None:
        """AC: User-provided value takes precedence over default."""
        schema = {
            "timeout": ConfigFieldSchema(
                type="integer", description="Timeout", default=30
            ),
        }
        result = validate_config(schema, {"timeout": 60})

        assert result.is_valid
        assert result.values["timeout"] == 60

    def test_multiple_errors_collected(self) -> None:
        """AC: Multiple validation failures are all reported."""
        schema = {
            "key1": ConfigFieldSchema(
                type="string", description="K1", required=True
            ),
            "key2": ConfigFieldSchema(
                type="integer", description="K2", required=True
            ),
        }
        result = validate_config(schema, {"key2": "wrong"})

        assert not result.is_valid
        assert len(result.diagnostics) == 2
        fields = {d.field for d in result.diagnostics}
        assert "key1" in fields
        assert "key2" in fields

    def test_valid_fields_preserved_alongside_errors(self) -> None:
        """AC: Valid fields appear in values even when other fields fail."""
        schema = {
            "good": ConfigFieldSchema(type="string", description="Good"),
            "bad": ConfigFieldSchema(type="integer", description="Bad"),
        }
        result = validate_config(schema, {"good": "ok", "bad": "wrong"})

        assert not result.is_valid
        assert result.values["good"] == "ok"

    def test_int_accepted_for_float_field(self) -> None:
        """AC: Integer value for float field is coerced to float."""
        schema = {
            "rate": ConfigFieldSchema(type="float", description="Rate"),
        }
        result = validate_config(schema, {"rate": 2})

        assert result.is_valid
        assert result.values["rate"] == 2.0
        assert isinstance(result.values["rate"], float)

    def test_negative_float_for_integer_field_zero_fraction(self) -> None:
        """AC: Negative float with zero fraction coerced to int."""
        schema = {
            "offset": ConfigFieldSchema(type="integer", description="Offset"),
        }
        result = validate_config(schema, {"offset": -5.0})

        assert result.is_valid
        assert result.values["offset"] == -5
        assert isinstance(result.values["offset"], int)

    def test_bool_rejected_for_integer(self) -> None:
        """AC: Boolean rejected for integer field (bool is int subclass)."""
        schema = {
            "count": ConfigFieldSchema(type="integer", description="Count"),
        }
        result = validate_config(schema, {"count": True})

        assert not result.is_valid
        assert "integer" in result.diagnostics[0].message

    def test_bool_rejected_for_float(self) -> None:
        """AC: Boolean rejected for float field."""
        schema = {
            "rate": ConfigFieldSchema(type="float", description="Rate"),
        }
        result = validate_config(schema, {"rate": False})

        assert not result.is_valid
        assert "float" in result.diagnostics[0].message

    def test_multiple_unknown_keys_collected(self) -> None:
        """AC: All unknown user keys are collected."""
        schema = {
            "key": ConfigFieldSchema(type="string", description="Key"),
        }
        result = validate_config(
            schema,
            {"key": "val", "extra1": 1, "extra2": 2},
        )

        assert result.is_valid
        assert set(result.unknown_keys) == {"extra1", "extra2"}
