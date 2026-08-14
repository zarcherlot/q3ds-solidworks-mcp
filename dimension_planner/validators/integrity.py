"""F1 handoff and frozen-artifact integrity gate for DimensionPlan."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from dimension_planner.handoff import (
    DimensionPlanningHandoffError,
    validate_dimension_planning_handoff,
)
from dimension_planner.planning_models import (
    DimensionPlanningRequest,
    DimensionValidationIssue,
)
from ._common import issue, same_path


class DimensionIntegrityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["pass", "fail"]
    handoff: dict[str, Any] | None
    issues: tuple[DimensionValidationIssue, ...] = ()

    @model_validator(mode="after")
    def validate_consistency(self) -> "DimensionIntegrityResult":
        if self.status == "pass" and (self.handoff is None or self.issues):
            raise ValueError("passing integrity result requires handoff and no issues")
        if self.status == "fail" and (self.handoff is not None or not self.issues):
            raise ValueError("failed integrity result requires issues and no handoff")
        return self


class DimensionPlanIntegrityValidator:
    def validate_request(
        self, request: DimensionPlanningRequest
    ) -> DimensionIntegrityResult:
        path = Path(request.handoff_path)
        if not path.is_file():
            return _failure("DP-INTEGRITY-HANDOFF-MISSING", "handoff does not exist")
        if _file_sha256(path) != request.handoff_sha256:
            return _failure(
                "DP-INTEGRITY-HANDOFF-HASH",
                "handoff SHA-256 does not match the planning request",
            )
        if not same_path(request.publication_directory, str(path.parent)):
            return _failure(
                "DP-INTEGRITY-PUBLICATION-LOCATION",
                "dimension_plan.json must be published beside the frozen handoff",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            handoff = validate_dimension_planning_handoff(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            DimensionPlanningHandoffError,
        ) as exc:
            return _failure("DP-INTEGRITY-HANDOFF-CONTRACT", str(exc))

        for artifact in handoff["upstream_artifacts"]:
            artifact_path = Path(artifact["path"])
            if not artifact_path.is_file():
                return _failure(
                    "DP-INTEGRITY-ARTIFACT-MISSING",
                    f"frozen artifact does not exist: {artifact_path}",
                )
            if _file_sha256(artifact_path) != artifact["sha256_before"]:
                return _failure(
                    "DP-INTEGRITY-ARTIFACT-HASH",
                    f"frozen artifact SHA-256 changed: {artifact_path}",
                )
        ledger = {row["role"]: row for row in handoff["upstream_artifacts"]}
        if not same_path(
            handoff["source_model"]["path"], ledger["source_model"]["path"]
        ) or not same_path(
            handoff["drawing_context"]["path"],
            ledger["verified_drawing"]["path"],
        ):
            return _failure(
                "DP-INTEGRITY-HANDOFF-BINDING",
                "handoff model/drawing context differs from its immutability ledger",
            )
        return DimensionIntegrityResult(status="pass", handoff=handoff)

    def validate_plan_bindings(
        self,
        plan: Mapping[str, Any],
        request: DimensionPlanningRequest,
    ) -> DimensionIntegrityResult:
        result = self.validate_request(request)
        if result.status == "fail":
            return result
        assert result.handoff is not None
        handoff = result.handoff
        ledger = {row["role"]: row for row in handoff["upstream_artifacts"]}
        bindings = (
            ("handoff", request.handoff_path, request.handoff_sha256),
            (
                "source_model",
                handoff["source_model"]["path"],
                handoff["source_model"]["sha256"],
            ),
            (
                "source_drawing",
                ledger["verified_drawing"]["path"],
                ledger["verified_drawing"]["sha256_before"],
            ),
            (
                "view_plan",
                ledger["view_plan"]["path"],
                ledger["view_plan"]["sha256_before"],
            ),
            (
                "verification_sidecar",
                ledger["verification_sidecar"]["path"],
                ledger["verification_sidecar"]["sha256_before"],
            ),
        )
        for field, expected_path, expected_hash in bindings:
            actual = plan.get(field)
            if not isinstance(actual, Mapping) or not same_path(
                actual.get("path"), expected_path
            ) or actual.get("sha256") != expected_hash:
                return _failure(
                    "DP-INTEGRITY-PLAN-BINDING",
                    f"DimensionPlan {field} does not match the frozen handoff",
                    f"/{field}",
                )
        expected_scalars = {
            "handoff_id": handoff["handoff_id"],
            "configuration": handoff["source_model"]["configuration"],
        }
        for field, expected in expected_scalars.items():
            if plan.get(field) != expected:
                return _failure(
                    "DP-INTEGRITY-PLAN-BINDING",
                    f"DimensionPlan {field} does not match the frozen handoff",
                    f"/{field}",
                )
        return result


def _failure(
    code: str,
    message: str,
    json_pointer: str | None = None,
) -> DimensionIntegrityResult:
    return DimensionIntegrityResult(
        status="fail",
        handoff=None,
        issues=(issue(code, "integrity", message, json_pointer),),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
