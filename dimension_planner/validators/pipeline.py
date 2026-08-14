"""Fixed-order fail-closed DimensionPlan validation pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dimension_planner.capability_registry import (
    DimensionCapabilityRegistry,
    current_registry,
)
from dimension_planner.planning_models import (
    DimensionPlanningRequest,
    DimensionPlanningValidation,
)
from ._common import issue
from .gates import (
    DimensionAttachmentValidator,
    DimensionCoverageValidator,
    DimensionLayoutValidator,
    DimensionRedundancyValidator,
    DimensionSemanticsValidator,
    DimensionSourceValidator,
)
from .integrity import DimensionPlanIntegrityValidator
from .schema import DimensionPlanSchemaValidator


_ENGINEERING_GATES = (
    "source",
    "attachment",
    "semantics",
    "coverage",
    "redundancy",
    "layout",
)


class RepositoryDimensionPlanValidator:
    """Run all nine F3 gates in a stable, fail-closed order."""

    def __init__(
        self,
        capabilities: DimensionCapabilityRegistry | None = None,
    ):
        self.capabilities = capabilities or current_registry()
        self._integrity = DimensionPlanIntegrityValidator()
        self._schema = DimensionPlanSchemaValidator()
        self._gates = (
            ("source", DimensionSourceValidator()),
            ("attachment", DimensionAttachmentValidator()),
            ("semantics", DimensionSemanticsValidator()),
            ("coverage", DimensionCoverageValidator()),
            ("redundancy", DimensionRedundancyValidator()),
            ("layout", DimensionLayoutValidator()),
        )

    def validate(
        self,
        plan: Mapping[str, Any],
        request: DimensionPlanningRequest,
    ) -> DimensionPlanningValidation:
        statuses = {gate: "not_run" for gate in _ENGINEERING_GATES}
        integrity = self._integrity.validate_plan_bindings(plan, request)
        if integrity.status == "fail":
            return self._result("fail", "not_run", statuses, "not_run", integrity.issues)

        schema_issues = self._schema.validate(plan)
        if schema_issues:
            return self._result("pass", "fail", statuses, "not_run", schema_issues)

        assert integrity.handoff is not None
        for gate, validator in self._gates:
            gate_issues = validator.validate(plan, integrity.handoff)
            statuses[gate] = "fail" if gate_issues else "pass"
            if gate_issues:
                return self._result("pass", "pass", statuses, "not_run", gate_issues)

        assessment = self.capabilities.assess(plan)
        if assessment.status == "capability_blocked":
            capability_issue = issue(
                "DP-CAPABILITY-BLOCKED",
                "capability",
                "executor lacks required capabilities: "
                + ", ".join(assessment.unsupported_capabilities),
                "/dimensions",
            )
            return self._result(
                "pass", "pass", statuses, "fail", (capability_issue,)
            )
        return self._result("pass", "pass", statuses, "pass", ())

    @staticmethod
    def _result(integrity, schema, statuses, capability, issues):
        return DimensionPlanningValidation(
            integrity=integrity,
            schema_check=schema,
            source=statuses["source"],
            attachment=statuses["attachment"],
            semantics=statuses["semantics"],
            coverage=statuses["coverage"],
            redundancy=statuses["redundancy"],
            layout=statuses["layout"],
            capability=capability,
            issues=issues,
        )
