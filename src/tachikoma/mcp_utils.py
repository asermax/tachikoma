"""Shared utilities for MCP tool servers.

Follows DES-006 (MCP tool server factory pattern).
"""

import json


def decode_json_string_array(raw: str, param_name: str) -> list[str]:
    """Decode a JSON-encoded string into a list of strings.

    The SDK MCP transport's client-side schema validator rejects array-typed
    tool arguments. Tools accept such parameters as JSON strings instead, and
    this helper performs the parse-and-validate step. See DES-006.

    Args:
        raw: JSON-encoded string (e.g., '["a", "b"]').
        param_name: Parameter name used in error messages.

    Raises:
        ValueError: when ``raw`` is not valid JSON, does not encode an array,
            or encodes an array containing non-string items.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{param_name} must be a JSON-encoded array of strings: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError(
            f"{param_name} JSON string must encode an array, got {type(decoded).__name__}"
        )
    if not all(isinstance(item, str) for item in decoded):
        raise ValueError(f"{param_name} JSON array must contain only strings")
    return decoded
