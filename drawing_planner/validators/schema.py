"""Draft 2020-12 structural validation for repository ViewPlan candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from drawing_planner.planning_models import ValidationIssue


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "view-plan.schema.json"
)


class ViewPlanSchemaValidator:
    def __init__(self, schema_path: Path = _SCHEMA_PATH):
        self.schema_path = schema_path.resolve()
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )

    def validate(self, plan: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
        if not isinstance(plan, Mapping):
            return (
                ValidationIssue(
                    code="VP-SCHEMA-ROOT",
                    gate="schema",
                    message="view plan root must be a JSON object",
                    json_pointer="",
                ),
            )
        errors = sorted(
            self._validator.iter_errors(dict(plan)),
            key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
        )
        return tuple(
            ValidationIssue(
                code="VP-SCHEMA-001",
                gate="schema",
                message=error.message,
                json_pointer=_json_pointer(error.absolute_path),
            )
            for error in errors
        )


def _json_pointer(path) -> str:
    parts = [str(value).replace("~", "~0").replace("/", "~1") for value in path]
    return "" if not parts else "/" + "/".join(parts)
