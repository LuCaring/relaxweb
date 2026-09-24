"""JSON Schema 2020-12 validation with contract-strict integer semantics."""

from __future__ import annotations

from typing import Any, Mapping

from jsonschema import Draft202012Validator, validators


# The default jsonschema integer checker accepts 400.0. Wire money, ticks,
# versions and configured quantities must be actual JSON integers in Python.
StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)


def validation_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    return [error.message for error in sorted(
        StrictValidator(schema).iter_errors(value), key=lambda error: (str(error.path), error.message)
    )]
