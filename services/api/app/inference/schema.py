"""Checking a model's answer against the JSON schema the caller asked for.

Model servers constrain generation to a schema, but none promise the result
matches it, so every answer is validated here before anyone can use it.
JSON Schema draft 2020-12, via `jsonschema`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.domain.inference import InvalidStructuredOutputError


def check_schema(schema: Mapping[str, Any]) -> None:
    """Raise `ValueError` for a schema that is not valid JSON Schema: a caller's mistake."""
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as exc:
        raise ValueError(f"the output schema is not valid JSON Schema: {exc.message}") from exc


def validate_output(document: object, schema: Mapping[str, Any]) -> None:
    """Raise `InvalidStructuredOutputError` unless `document` matches `schema`."""
    check_schema(schema)
    # A plain dict: jsonschema's "object" check does not accept a read-only mapping.
    validator = Draft202012Validator(dict(schema))
    error = next(iter(validator.iter_errors(cast(Any, document))), None)
    if error is not None:
        # The path and rule only: the offending value may be text from the prompt.
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise InvalidStructuredOutputError(
            f"the answer does not match the schema at {where}: {error.validator} failed"
        )
