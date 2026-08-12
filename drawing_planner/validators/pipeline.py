"""Ordered deterministic validation pipeline for repository ViewPlan candidates."""

from __future__ import annotations

from drawing_planner.planning_models import (
    PlanningRequest,
    PlanningValidation,
)
from drawing_planner.planner_profiles import producer_contract_for_profile
from drawing_planner.validators._common import validation_issue
from drawing_planner.validators.integrity import HandoffIntegrityValidator
from drawing_planner.validators.coverage import ViewPlanCoverageValidator
from drawing_planner.validators.layout import ViewPlanLayoutValidator
from drawing_planner.validators.schema import ViewPlanSchemaValidator
from drawing_planner.validators.semantics import ViewPlanSemanticsValidator


class RepositoryViewPlanValidator:
    """Fail closed in fixed integrity/schema/semantics/coverage/layout order."""

    def __init__(self):
        self._integrity = HandoffIntegrityValidator()
        self._schema = ViewPlanSchemaValidator()
        self._semantics = ViewPlanSemanticsValidator()
        self._coverage = ViewPlanCoverageValidator()
        self._layout = ViewPlanLayoutValidator()

    def validate(self, plan: dict, request: PlanningRequest) -> PlanningValidation:
        integrity = self._integrity.validate_plan_bindings(plan, request)
        if integrity.status == "fail":
            return PlanningValidation(
                integrity="fail",
                schema_check="not_run",
                semantics="not_run",
                coverage="not_run",
                layout="not_run",
                issues=integrity.issues,
            )
        schema_issues = self._schema.validate(plan)
        if schema_issues:
            return PlanningValidation(
                integrity="pass",
                schema_check="fail",
                semantics="not_run",
                coverage="not_run",
                layout="not_run",
                issues=schema_issues,
            )
        try:
            expected_producer = producer_contract_for_profile(request.planner_profile)
        except ValueError as exc:
            semantic_issues = (
                validation_issue(
                    "VP-SEMANTICS-PLANNER-PROFILE",
                    "semantics",
                    str(exc),
                    "/producer",
                ),
            )
        else:
            semantic_issues = self._semantics.validate(
                plan,
                expected_producer=expected_producer,
            )
        coverage_issues = self._coverage.validate(
            plan,
            semantic_artifact=integrity.semantic_artifact,
        )
        layout_issues = self._layout.validate(plan)
        issues = (*semantic_issues, *coverage_issues, *layout_issues)
        return PlanningValidation(
            integrity="pass",
            schema_check="pass",
            semantics="fail" if semantic_issues else "pass",
            coverage="fail" if coverage_issues else "pass",
            layout="fail" if layout_issues else "pass",
            issues=issues,
        )


# Transitional import compatibility for callers created during A3/A4.
FoundationViewPlanValidator = RepositoryViewPlanValidator
