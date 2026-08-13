"""Validate, capability-assess and atomically publish one DimensionPlan candidate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dimension_planner.capability_registry import (
    DimensionCapabilityRegistry,
    current_registry,
)
from dimension_planner.plan_store import PlanStore
from dimension_planner.planning_models import (
    DimensionPlanningAudit,
    DimensionPlanningRequest,
    DimensionPlanningResult,
    canonical_json_sha256,
    json_object_copy,
)
from dimension_planner.validators import RepositoryDimensionPlanValidator


class DimensionPlannerEngine:
    """F3 publication boundary; model generation remains an upper-layer concern."""

    def __init__(
        self,
        *,
        validator: RepositoryDimensionPlanValidator | None = None,
        capabilities: DimensionCapabilityRegistry | None = None,
        plan_store: PlanStore | None = None,
    ):
        self._capabilities = capabilities or (
            validator.capabilities if validator is not None else current_registry()
        )
        self._validator = validator or RepositoryDimensionPlanValidator(
            self._capabilities
        )
        self._plan_store = plan_store or PlanStore()

    def validate_and_publish(
        self,
        candidate: Mapping[str, Any],
        request: DimensionPlanningRequest,
    ) -> DimensionPlanningResult:
        plan = json_object_copy(candidate, "DimensionPlan candidate")
        request_hash = canonical_json_sha256(
            request.model_dump(mode="json"), "dimension planning request"
        )
        candidate_hash = canonical_json_sha256(plan, "DimensionPlan candidate")
        validation = self._validator.validate(plan, request)
        if not validation.engineering_passed:
            return DimensionPlanningResult(
                status="rejected",
                execution_readiness="not_assessed",
                validation=validation,
                plan=None,
                audit=DimensionPlanningAudit(
                    request_sha256=request_hash,
                    candidate_sha256=candidate_hash,
                ),
            )

        assessment = self._capabilities.assess(plan)
        published = self._plan_store.publish(plan, request.publication_directory)
        return DimensionPlanningResult(
            status="published",
            execution_readiness=assessment.status,
            validation=validation,
            plan=published,
            audit=DimensionPlanningAudit(
                request_sha256=request_hash,
                candidate_sha256=candidate_hash,
                capability_manifest_version=assessment.manifest_version,
            ),
            unsupported_capabilities=assessment.unsupported_capabilities,
        )
