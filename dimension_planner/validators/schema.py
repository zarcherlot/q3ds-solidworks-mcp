"""Draft 2020-12 structural validation for DimensionPlan 1.0."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from dimension_planner.planning_models import DimensionValidationIssue
from ._common import issue, pointer, stable_issues


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "dimension-plan.schema.json"
)


class DimensionPlanSchemaValidator:
    def __init__(self, schema_path: Path = _SCHEMA_PATH):
        schema = json.loads(schema_path.resolve().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )

    def validate(
        self, plan: Mapping[str, Any]
    ) -> tuple[DimensionValidationIssue, ...]:
        if not isinstance(plan, Mapping):
            return (
                issue(
                    "DP-SCHEMA-ROOT",
                    "schema",
                    "dimension plan root must be a JSON object",
                    "",
                ),
            )
        errors = sorted(
            self._validator.iter_errors(dict(plan)),
            key=lambda error: (
                tuple(str(item) for item in error.absolute_path),
                error.message,
            ),
        )
        return stable_issues(
            issue(
                "DP-SCHEMA-001",
                "schema",
                error.message,
                pointer(*error.absolute_path),
            )
            for error in errors
        )
