"""Feature-to-view coverage validation for frozen ViewPlan requirements."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from drawing_planner.planning_models import ValidationIssue
from drawing_planner.validators._common import pointer, stable_issues, validation_issue


_SECTION_TYPES = {
    "full_section",
    "half_section",
    "offset_section",
    "aligned_section",
    "removed_section",
}
_MODE_VIEW_TYPES = {
    "direct_opening_view": {
        "model_view",
        "projected_view",
        "detail_view",
        "auxiliary_view",
        "broken_out_section",
    },
    "section_through_axis": _SECTION_TYPES,
    "true_shape_view": {
        "model_view",
        "projected_view",
        "detail_view",
        "auxiliary_view",
    },
    "cross_section": _SECTION_TYPES | {"broken_out_section"},
    "direct_visible_profile": {
        "model_view",
        "projected_view",
        "full_section",
        "half_section",
        "offset_section",
        "aligned_section",
        "removed_section",
        "broken_out_section",
        "detail_view",
        "auxiliary_view",
    },
}


class ViewPlanCoverageValidator:
    def validate(self, plan: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        views = {view["id"]: view for view in plan["views"]}
        coverage_rows = plan["feature_coverage"]
        feature_counts = Counter(row["feature_id"] for row in coverage_rows)
        for feature_index, row in enumerate(coverage_rows):
            feature_id = row["feature_id"]
            if feature_counts[feature_id] > 1:
                issues.append(
                    validation_issue(
                        "VP-COVERAGE-DUPLICATE-FEATURE",
                        "coverage",
                        f"feature_coverage repeats feature ID: {feature_id}",
                        pointer("feature_coverage", feature_index, "feature_id"),
                    )
                )
            requirement_counts = Counter(
                requirement["requirement_id"] for requirement in row["requirements"]
            )
            for requirement_index, requirement in enumerate(row["requirements"]):
                base = pointer(
                    "feature_coverage",
                    feature_index,
                    "requirements",
                    requirement_index,
                )
                requirement_id = requirement["requirement_id"]
                if requirement_counts[requirement_id] > 1:
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-DUPLICATE-REQUIREMENT",
                            "coverage",
                            f"feature {feature_id} repeats requirement ID: {requirement_id}",
                            base + "/requirement_id",
                        )
                    )
                if requirement["status"] != "pass":
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-STATUS",
                            "coverage",
                            f"coverage requirement is not satisfied: {feature_id}/{requirement_id}",
                            base + "/status",
                        )
                    )
                if requirement["required_mode"] != requirement["expression_mode"]:
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-MODE-MISMATCH",
                            "coverage",
                            "expression_mode must exactly match required_mode",
                            base + "/expression_mode",
                        )
                    )
                target = views.get(requirement["satisfied_by"])
                if target is None:
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-VIEW-MISSING",
                            "coverage",
                            f"satisfied_by does not reference a planned view: {requirement['satisfied_by']}",
                            base + "/satisfied_by",
                        )
                    )
                    continue
                if feature_id not in target["expressed_features"]:
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-FEATURE-NOT-EXPRESSED",
                            "coverage",
                            f"view {target['id']} does not declare feature {feature_id}",
                            base + "/satisfied_by",
                        )
                    )
                mode = requirement["expression_mode"]
                if target["type"] not in _MODE_VIEW_TYPES[mode]:
                    issues.append(
                        validation_issue(
                            "VP-COVERAGE-INCOMPATIBLE-VIEW",
                            "coverage",
                            f"view type {target['type']} cannot satisfy {mode}",
                            base + "/satisfied_by",
                        )
                    )
                if mode == "section_through_axis":
                    section = target["section_definition"]
                    if section is None or feature_id not in section["feature_ids"]:
                        issues.append(
                            validation_issue(
                                "VP-COVERAGE-SECTION-FEATURE",
                                "coverage",
                                "section_through_axis requires the feature in section_definition.feature_ids",
                                base + "/satisfied_by",
                            )
                        )

        coverage_ids = set(feature_counts)
        forced_references = _forced_feature_references(plan)
        for feature_id, feature_pointer in forced_references:
            if feature_id not in coverage_ids:
                issues.append(
                    validation_issue(
                        "VP-COVERAGE-FEATURE-MISSING",
                        "coverage",
                        f"feature requiring a frozen expression has no feature_coverage row: {feature_id}",
                        feature_pointer,
                    )
                )
        for feature_index, row in enumerate(coverage_rows):
            feature_id = row["feature_id"]
            if not any(feature_id in view["expressed_features"] for view in views.values()):
                issues.append(
                    validation_issue(
                        "VP-COVERAGE-UNEXPRESSED-FEATURE",
                        "coverage",
                        f"covered feature is not expressed by any planned view: {feature_id}",
                        pointer("feature_coverage", feature_index, "feature_id"),
                    )
                )
        return stable_issues(issues)


def _forced_feature_references(plan):
    rows: list[tuple[str, str]] = []
    for view_index, view in enumerate(plan["views"]):
        for mark_index, mark in enumerate(view["center_marks"]):
            for feature_index, feature_id in enumerate(mark["feature_ids"]):
                rows.append(
                    (
                        feature_id,
                        pointer(
                            "views",
                            view_index,
                            "center_marks",
                            mark_index,
                            "feature_ids",
                            feature_index,
                        ),
                    )
                )
        section = view["section_definition"]
        if section is not None:
            for feature_index, feature_id in enumerate(section["feature_ids"]):
                rows.append(
                    (
                        feature_id,
                        pointer(
                            "views",
                            view_index,
                            "section_definition",
                            "feature_ids",
                            feature_index,
                        ),
                    )
                )
    return rows
