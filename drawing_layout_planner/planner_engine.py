"""G3 deterministic DrawingLayoutPlan generation and publication boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from drawing_planner.planning_models import canonical_json_sha256, json_object_copy

from .capability_registry import DrawingLayoutCapabilityRegistry, current_registry
from .engine_models import (
    LayoutPlanningAudit,
    LayoutPlanningRequest,
    LayoutPlanningResult,
    LayoutPlanningValidation,
    LayoutValidationIssue,
)
from .handoff import DrawingLayoutHandoffError, validate_drawing_layout_handoff
from .layout_solver import RepositoryLayoutSolver, SolverOutcome
from .plan_store import PlanStore
from .planning_models import DrawingLayoutPlan, drawing_layout_plan_from_mapping


class DrawingLayoutPlannerEngine:
    """Resolve layout intent, run every G3 gate and publish one frozen plan."""

    def __init__(
        self,
        *,
        solver: RepositoryLayoutSolver | None = None,
        capabilities: DrawingLayoutCapabilityRegistry | None = None,
        plan_store: PlanStore | None = None,
    ):
        self._solver = solver or RepositoryLayoutSolver()
        self._capabilities = capabilities or current_registry()
        self._plan_store = plan_store or PlanStore()

    def plan(
        self, request: LayoutPlanningRequest | Mapping[str, Any]
    ) -> LayoutPlanningResult:
        normalized_request, candidate, validation, audit = self._resolve_candidate(request)
        if candidate is None or not validation.passed:
            return LayoutPlanningResult(
                status="rejected",
                execution_readiness="not_assessed",
                validation=validation,
                plan=None,
                audit=audit,
            )

        assessment = self._capabilities.assess(candidate)
        published = self._plan_store.publish(
            candidate, normalized_request.publication_directory
        )
        return LayoutPlanningResult(
            status="published",
            execution_readiness=assessment.status,
            validation=validation,
            plan=published,
            audit=LayoutPlanningAudit(
                request_sha256=audit.request_sha256,
                handoff_sha256=audit.handoff_sha256,
                ruleset_sha256=audit.ruleset_sha256,
                candidate_sha256=candidate.canonical_sha256,
                capability_manifest_version=assessment.manifest_version,
            ),
            unsupported_capabilities=assessment.unsupported_capabilities,
        )

    def validate_plan(
        self,
        plan: DrawingLayoutPlan | Mapping[str, Any],
        request: LayoutPlanningRequest | Mapping[str, Any],
    ):
        """Recompute one request without publication and reject any plan drift."""
        normalized_plan = (
            plan
            if isinstance(plan, DrawingLayoutPlan)
            else drawing_layout_plan_from_mapping(plan)
        )
        normalized_request, candidate, validation, audit = self._resolve_candidate(request)
        if candidate is not None and validation.passed and (
            candidate.execution_dict() != normalized_plan.execution_dict()
        ):
            validation = _validation(
                (
                    _integrity_issue(
                        "published-plan-determinism-mismatch",
                        "the supplied DrawingLayoutPlan differs from the unique plan resolved from the unchanged request",
                    ),
                ),
                integrity_passed=False,
            )
        assessment = (
            self._capabilities.assess(normalized_plan) if validation.passed else None
        )
        return normalized_plan, normalized_request, validation, assessment, audit

    def _resolve_candidate(
        self, request: LayoutPlanningRequest | Mapping[str, Any]
    ) -> tuple[
        LayoutPlanningRequest,
        DrawingLayoutPlan | None,
        LayoutPlanningValidation,
        LayoutPlanningAudit,
    ]:
        normalized_request = (
            request
            if isinstance(request, LayoutPlanningRequest)
            else LayoutPlanningRequest.model_validate(
                json_object_copy(request, "layout planning request")
            )
        )
        request_payload = normalized_request.model_dump(mode="json")
        request_sha256 = canonical_json_sha256(
            request_payload, "layout planning request"
        )
        handoff, integrity_issues = self._load_and_verify_handoff(normalized_request)
        audit = LayoutPlanningAudit(
            request_sha256=request_sha256,
            handoff_sha256=normalized_request.handoff.sha256,
            ruleset_sha256=self._solver.ruleset_sha256,
        )
        if integrity_issues:
            return (
                normalized_request,
                None,
                _validation(tuple(integrity_issues), integrity_passed=False),
                audit,
            )
        assert handoff is not None

        outcome = self._solver.solve(
            handoff, normalized_request.intents, normalized_request.authorization
        )
        issues = list(outcome.issues)
        if not outcome.operations:
            issues.append(
                LayoutValidationIssue(
                    gate="solver",
                    code="no-layout-operations",
                    message="the deterministic solver produced no legal layout operation",
                )
            )
        candidate: DrawingLayoutPlan | None = None
        if not issues:
            try:
                candidate = drawing_layout_plan_from_mapping(
                    self._build_candidate(normalized_request, handoff, outcome)
                )
            except (ValidationError, ValueError) as exc:
                issues.append(
                    LayoutValidationIssue(
                        gate="solver",
                        code="plan-contract-rejected",
                        message=f"solved operations violate DrawingLayoutPlan 1.0: {exc}",
                    )
                )
        validation = _validation(tuple(issues), integrity_passed=True)
        return normalized_request, candidate, validation, audit

    def _load_and_verify_handoff(
        self, request: LayoutPlanningRequest
    ) -> tuple[dict[str, Any] | None, list[LayoutValidationIssue]]:
        issues: list[LayoutValidationIssue] = []
        path = Path(request.handoff.path)
        if not path.is_file():
            return None, [
                _integrity_issue(
                    "handoff-file-missing", "the bound G1 handoff file does not exist", str(path)
                )
            ]
        actual_handoff_sha256 = _file_sha256(path)
        if actual_handoff_sha256 != request.handoff.sha256:
            issues.append(
                _integrity_issue(
                    "handoff-sha256-mismatch",
                    "the G1 handoff bytes changed after request construction",
                    str(path),
                )
            )
            return None, issues
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("JSON root is not an object")
            handoff = validate_drawing_layout_handoff(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            DrawingLayoutHandoffError,
            ValueError,
        ) as exc:
            issues.append(
                _integrity_issue(
                    "handoff-contract-invalid", f"G1 handoff validation failed: {exc}"
                )
            )
            return None, issues
        if handoff["status"] != "ready" or handoff["boundary_capabilities"][
            "unsupported"
        ]:
            issues.append(
                _integrity_issue(
                    "handoff-capability-blocked",
                    "G3 cannot solve legal coordinates from unsupported exact boundaries",
                    *handoff["boundary_capabilities"]["unsupported"],
                )
            )
        roles: dict[str, dict[str, Any]] = {}
        for row in handoff["upstream_artifacts"]:
            role = row["role"]
            roles[role] = row
            upstream = Path(row["path"])
            if not upstream.is_file():
                issues.append(
                    _integrity_issue(
                        "upstream-file-missing",
                        "a frozen G1 upstream artifact no longer exists",
                        role,
                        str(upstream),
                    )
                )
                continue
            if _file_sha256(upstream) != row["sha256_after"]:
                issues.append(
                    _integrity_issue(
                        "upstream-sha256-mismatch",
                        "a frozen G1 upstream artifact changed",
                        role,
                        str(upstream),
                    )
                )
        for row in handoff["objects"]:
            if row["category"] in handoff["boundary_capabilities"]["required"] and (
                row["exact"] is not True or row["collision_usable"] is not True
            ):
                issues.append(
                    _integrity_issue(
                        "required-boundary-inexact",
                        "a required G0 boundary is not exact and collision-usable",
                        row["id"],
                        row["category"],
                    )
                )
        boundary_row = roles.get("boundary_capability_manifest")
        if boundary_row is not None and (
            boundary_row["sha256_after"]
            != self._capabilities.boundary_manifest_sha256
            or handoff["boundary_capabilities"]["registry_version"]
            != self._capabilities.manifest.boundary_registry.registry_version
        ):
            issues.append(
                _integrity_issue(
                    "boundary-registry-binding-mismatch",
                    "G1 does not bind the same G0 registry version and bytes as G3",
                    boundary_row["path"],
                )
            )
        plan_row = roles.get("dimension_plan")
        if plan_row is not None and Path(plan_row["path"]).is_file():
            expected_plan_path = (
                Path(request.source_dimension_request.publication_directory)
                .resolve()
                / "dimension_plan.json"
            )
            if Path(plan_row["path"]).resolve() != expected_plan_path:
                issues.append(
                    _integrity_issue(
                        "dimension-request-publication-mismatch",
                        "the G1 DimensionPlan is not the plan published by the bound dimension request",
                        str(expected_plan_path),
                    )
                )
            dimension_handoff = Path(
                request.source_dimension_request.handoff_path
            )
            if not dimension_handoff.is_file() or (
                _file_sha256(dimension_handoff)
                != request.source_dimension_request.handoff_sha256
            ):
                issues.append(
                    _integrity_issue(
                        "dimension-request-handoff-mismatch",
                        "the immutable dimension request no longer binds its original handoff bytes",
                        str(dimension_handoff),
                    )
                )
            try:
                dimension_plan = json.loads(
                    Path(plan_row["path"]).read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                issues.append(
                    _integrity_issue(
                        "dimension-plan-invalid",
                        f"the frozen DimensionPlan cannot be read: {exc}",
                    )
                )
            else:
                if (
                    not isinstance(dimension_plan, dict)
                    or dimension_plan.get("protocol_id") != "solidworks-dimension-plan"
                    or dimension_plan.get("schema_version") != "1.0"
                    or dimension_plan.get("plan_id")
                    != handoff["dimension_semantics"]["plan_id"]
                    or not isinstance(dimension_plan.get("configuration"), str)
                    or not dimension_plan["configuration"]
                ):
                    issues.append(
                        _integrity_issue(
                            "dimension-plan-continuity-invalid",
                            "DimensionPlan protocol, ID or configuration does not match G1",
                        )
                    )
        return handoff, issues

    def _build_candidate(
        self,
        request: LayoutPlanningRequest,
        handoff: Mapping[str, Any],
        outcome: SolverOutcome,
    ) -> dict[str, Any]:
        roles = {row["role"]: row for row in handoff["upstream_artifacts"]}
        dimension_plan_path = Path(roles["dimension_plan"]["path"])
        dimension_plan = json.loads(dimension_plan_path.read_text(encoding="utf-8"))
        view_names = tuple(sorted(outcome.state.view_positions))
        return {
            "$schema": "https://q3ds.local/contracts/solidworks-drawing-layout-plan-1.0.schema.json",
            "protocol_id": "solidworks-drawing-layout-plan",
            "schema_version": "1.0",
            "plan_id": request.plan_id,
            "created_at_utc": request.created_at_utc,
            "producer": {
                "name": outcome.ruleset["producer_name"],
                "version": outcome.ruleset["producer_version"],
                "ruleset_id": outcome.ruleset["ruleset_id"],
                "ruleset_sha256": outcome.ruleset_sha256,
            },
            "execution_policy": {
                "on_integrity_mismatch": "fail",
                "on_layout_violation": "fail",
                "on_unsupported_operation": "fail",
                "preserve_dimension_count": True,
                "preserve_dimension_values": True,
                "preserve_dimension_attachments": True,
                "preserve_configuration": True,
                "preserve_display_state": True,
                "preserve_projection_method": True,
                "preserve_section_definitions": True,
                "preserve_model_associativity": True,
                "preserve_frozen_geometry": True,
                "allow_delete_objects": False,
                "allow_new_manufacturing_annotations": False,
                "allow_source_model_write": False,
                "allow_upstream_drawing_overwrite": False,
                "allow_partial_commit": False,
            },
            "handoff": {
                "path": request.handoff.path,
                "sha256": request.handoff.sha256,
            },
            "handoff_id": handoff["handoff_id"],
            "source_dimension_plan": {
                "path": roles["dimension_plan"]["path"],
                "sha256": roles["dimension_plan"]["sha256_after"],
            },
            "source_drawing": {
                "path": roles["dimensioned_drawing"]["path"],
                "sha256": roles["dimensioned_drawing"]["sha256_after"],
            },
            "dimension_verification_sidecar": {
                "path": roles["dimension_verification_sidecar"]["path"],
                "sha256": roles["dimension_verification_sidecar"]["sha256_after"],
            },
            "configuration": dimension_plan["configuration"],
            "source_invariants": {
                "dimension_semantics_sha256": handoff["dimension_semantics"][
                    "invariant_sha256"
                ],
                "dimension_ids": [
                    row["dimension_id"]
                    for row in handoff["dimension_semantics"]["dimensions"]
                ],
                "object_snapshot_sha256": handoff["snapshots"][
                    "readonly_reopen_sha256"
                ],
                "object_ids": [row["id"] for row in handoff["objects"]],
                "view_names": list(view_names),
                "locked_object_ids": list(handoff["constraints"]["frozen_objects"]),
                "required_boundary_capabilities": list(
                    handoff["boundary_capabilities"]["required"]
                ),
            },
            "authorization": request.authorization.model_dump(mode="json"),
            "operations": list(outcome.operations),
            "assumptions": list(request.assumptions),
            "open_questions": [],
        }


def _validation(
    issues: tuple[LayoutValidationIssue, ...], *, integrity_passed: bool
) -> LayoutPlanningValidation:
    if not integrity_passed:
        return LayoutPlanningValidation(
            integrity="fail",
            phase_order="not_run",
            safe_area="not_run",
            locked_zones="not_run",
            collisions="not_run",
            dimension_crossing="not_run",
            projection_alignment="not_run",
            minimum_spacing="not_run",
            readability="not_run",
            solver="not_run",
            issues=issues,
        )
    gates = {
        "phase_order",
        "safe_area",
        "locked_zones",
        "collisions",
        "dimension_crossing",
        "projection_alignment",
        "minimum_spacing",
        "readability",
        "solver",
    }
    failed = {issue.gate for issue in issues}
    return LayoutPlanningValidation(
        integrity="pass",
        **{gate: "fail" if gate in failed else "pass" for gate in gates},
        issues=issues,
    )


def _integrity_issue(
    code: str, message: str, *references: str
) -> LayoutValidationIssue:
    return LayoutValidationIssue(
        gate="integrity",
        code=code,
        message=message,
        references=tuple(references),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
