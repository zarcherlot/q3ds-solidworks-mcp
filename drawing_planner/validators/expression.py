"""Deterministic feature-expression gates for experimental ViewPlan 1.5 candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from drawing_planner.planning_models import ValidationIssue
from drawing_planner.semantic_features import ModelSemanticFeatures, SemanticFeature
from drawing_planner.validators._common import pointer, stable_issues, validation_issue


_SECTION_TYPES = {
    "full_section", "half_section", "offset_section", "aligned_section", "removed_section"
}
_METHOD_VIEW_TYPES = {
    "direct_opening_view": {
        "model_view", "projected_view", "detail_view", "auxiliary_view", "broken_out_section"
    },
    "section_through_axis": _SECTION_TYPES,
    "true_shape_view": {"model_view", "projected_view", "detail_view", "auxiliary_view"},
    "cross_section": _SECTION_TYPES | {"broken_out_section"},
    "direct_visible_profile": {
        "model_view", "projected_view", *_SECTION_TYPES, "broken_out_section", "detail_view",
        "auxiliary_view"
    },
    "independent_multiview": {
        "model_view", "projected_view", *_SECTION_TYPES, "detail_view", "auxiliary_view"
    },
}
_STANDARD_DIRECTIONS = {
    "front": (0.0, 0.0, -1.0),
    "back": (0.0, 0.0, 1.0),
    "left": (1.0, 0.0, 0.0),
    "right": (-1.0, 0.0, 0.0),
    "top": (0.0, -1.0, 0.0),
    "bottom": (0.0, 1.0, 0.0),
}


class ViewPlan15ExpressionValidator:
    """Validate 1.5 view sets, independent projections, true shape, counts, and depth evidence."""

    def validate(
        self,
        plan: Mapping[str, Any],
        *,
        semantic_artifact: ModelSemanticFeatures | None = None,
        geometry_report: Mapping[str, Any] | None = None,
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        views = {view["id"]: view for view in plan["views"]}
        features = (
            {feature.feature_id: feature for feature in semantic_artifact.features}
            if semantic_artifact is not None else {}
        )
        relations = (
            {relation.relation_id: relation for relation in semantic_artifact.relations}
            if semantic_artifact is not None else {}
        )
        coverage_counts = Counter(row["feature_id"] for row in plan["feature_coverage"])

        for row_index, row in enumerate(plan["feature_coverage"]):
            feature_id = row["feature_id"]
            if coverage_counts[feature_id] > 1:
                issues.append(_issue(
                    "VP15-EXPRESSION-DUPLICATE-FEATURE",
                    f"feature_coverage repeats feature ID: {feature_id}",
                    pointer("feature_coverage", row_index, "feature_id"),
                ))
            feature = features.get(feature_id)
            if semantic_artifact is not None and feature is None:
                issues.append(_issue(
                    "VP15-EXPRESSION-UNKNOWN-FEATURE",
                    f"feature_coverage references an unknown semantic feature: {feature_id}",
                    pointer("feature_coverage", row_index, "feature_id"),
                ))
            elif feature is not None and feature.feature_class != row["feature_class"]:
                issues.append(_issue(
                    "VP15-EXPRESSION-FEATURE-CLASS",
                    "feature_class does not match the frozen semantic artifact",
                    pointer("feature_coverage", row_index, "feature_class"),
                ))

            requirement_counts = Counter(
                requirement["requirement_id"] for requirement in row["requirements"]
            )
            for requirement_index, requirement in enumerate(row["requirements"]):
                base = pointer("feature_coverage", row_index, "requirements", requirement_index)
                if requirement_counts[requirement["requirement_id"]] > 1:
                    issues.append(_issue(
                        "VP15-EXPRESSION-DUPLICATE-REQUIREMENT",
                        f"feature {feature_id} repeats requirement ID: {requirement['requirement_id']}",
                        base + "/requirement_id",
                    ))
                if requirement["status"] != "pass":
                    issues.append(_issue(
                        "VP15-EXPRESSION-STATUS",
                        f"expression requirement is not satisfied: {feature_id}/{requirement['requirement_id']}",
                        base + "/status",
                    ))
                targets = requirement["satisfied_by"]
                primary_count = sum(target["role"] == "primary" for target in targets)
                if primary_count != 1:
                    issues.append(_issue(
                        "VP15-EXPRESSION-PRIMARY-VIEW",
                        "satisfied_by must contain exactly one primary view",
                        base + "/satisfied_by",
                    ))
                target_ids = [target["view_id"] for target in targets]
                if len(target_ids) != len(set(target_ids)):
                    issues.append(_issue(
                        "VP15-EXPRESSION-DUPLICATE-VIEW",
                        "satisfied_by cannot repeat a view with another role",
                        base + "/satisfied_by",
                    ))

                resolved = []
                method = requirement["expression_method"]
                for target_index, target in enumerate(targets):
                    view = views.get(target["view_id"])
                    target_pointer = base + f"/satisfied_by/{target_index}/view_id"
                    if view is None:
                        issues.append(_issue(
                            "VP15-EXPRESSION-VIEW-MISSING",
                            f"satisfied_by does not reference a planned view: {target['view_id']}",
                            target_pointer,
                        ))
                        continue
                    resolved.append(view)
                    if feature_id not in view["expressed_features"]:
                        issues.append(_issue(
                            "VP15-EXPRESSION-FEATURE-NOT-EXPRESSED",
                            f"view {view['id']} does not declare feature {feature_id}",
                            target_pointer,
                        ))
                    if view["type"] not in _METHOD_VIEW_TYPES[method]:
                        issues.append(_issue(
                            "VP15-EXPRESSION-INCOMPATIBLE-VIEW",
                            f"view type {view['type']} cannot satisfy {method}",
                            target_pointer,
                        ))
                    if method == "section_through_axis":
                        section = view["section_definition"]
                        if section is None or feature_id not in section["feature_ids"]:
                            issues.append(_issue(
                                "VP15-EXPRESSION-SECTION-FEATURE",
                                "section_through_axis requires the feature in section_definition.feature_ids",
                                target_pointer,
                            ))

                directions = [_view_direction(view, views) for view in resolved]
                independent_count = _independent_projection_count(
                    [direction for direction in directions if direction is not None]
                )
                required_count = requirement["minimum_independent_projections"]
                if independent_count < required_count:
                    issues.append(_issue(
                        "VP15-EXPRESSION-INDEPENDENT-PROJECTIONS",
                        f"requires {required_count} independent projections but only {independent_count} are proven",
                        base + "/minimum_independent_projections",
                    ))

                if method == "true_shape_view" and feature is not None:
                    primary = next(
                        (views.get(target["view_id"]) for target in targets if target["role"] == "primary"),
                        None,
                    )
                    if primary is not None and not _proves_true_shape(primary, feature, views):
                        issues.append(_issue(
                            "VP15-EXPRESSION-TRUE-SHAPE-DIRECTION",
                            "primary view direction is not parallel to the frozen feature normal or axis",
                            base + "/satisfied_by",
                        ))

                if feature is not None:
                    _validate_frozen_feature_evidence(feature, requirement, base, issues)
                    _validate_relations(
                        feature_id, requirement, relations, base, issues
                    )
                    _validate_spatial_direction(
                        feature, requirement, relations, base, issues
                    )
                    _validate_section_preconditions(
                        feature,
                        requirement,
                        targets,
                        views,
                        relations,
                        base,
                        issues,
                    )
                    _validate_discernibility(
                        feature,
                        requirement,
                        targets,
                        views,
                        geometry_report,
                        base,
                        issues,
                    )

        return stable_issues(issues)


def _validate_frozen_feature_evidence(feature, requirement, base, issues) -> None:
    kind = requirement["requirement_kind"]
    if kind == "opening_and_count":
        if feature.opening_count is None:
            issues.append(_issue(
                "VP15-EXPRESSION-OPENING-COUNT-UNPROVEN",
                "opening_and_count requires a frozen opening_count",
                base + "/requirement_kind",
            ))
        elif requirement["expected_opening_count"] != feature.opening_count:
            issues.append(_issue(
                "VP15-EXPRESSION-OPENING-COUNT-MISMATCH",
                "expected_opening_count does not equal the frozen semantic opening_count",
                base + "/expected_opening_count",
            ))
        actual_occurrences = sum(not occurrence.suppressed for occurrence in feature.occurrences)
        if requirement["expected_unsuppressed_occurrence_count"] != actual_occurrences:
            issues.append(_issue(
                "VP15-EXPRESSION-OCCURRENCE-COUNT-MISMATCH",
                "expected_unsuppressed_occurrence_count does not equal frozen occurrences",
                base + "/expected_unsuppressed_occurrence_count",
            ))
    if kind == "depth_extent":
        if feature.axial_extent is None:
            issues.append(_issue(
                "VP15-EXPRESSION-DEPTH-UNPROVEN",
                "depth_extent requires a frozen B-Rep axial_extent",
                base + "/requirement_kind",
            ))
        else:
            if not _same_number(
                requirement["expected_effective_depth_m"],
                feature.axial_extent.effective_depth_m,
            ):
                issues.append(_issue(
                    "VP15-EXPRESSION-EFFECTIVE-DEPTH-MISMATCH",
                    "expected_effective_depth_m does not equal frozen B-Rep evidence",
                    base + "/expected_effective_depth_m",
                ))
            if not _same_number(
                requirement["expected_total_depth_m"], feature.axial_extent.total_depth_m
            ):
                issues.append(_issue(
                    "VP15-EXPRESSION-TOTAL-DEPTH-MISMATCH",
                    "expected_total_depth_m does not equal frozen B-Rep evidence",
                    base + "/expected_total_depth_m",
                ))


def _validate_relations(feature_id, requirement, relations, base, issues) -> None:
    kind = requirement["requirement_kind"]
    accepted_classes = {
        "pattern_relation": {"relation.pattern", "relation.symmetry_or_mirror"},
        "location_relation": {
            "relation.pattern", "relation.symmetry_or_mirror",
            "relation.coaxial_or_intersecting",
        },
    }.get(kind)
    if accepted_classes is None:
        return
    for index, relation_id in enumerate(requirement["semantic_relation_ids"]):
        relation = relations.get(relation_id)
        relation_pointer = base + f"/semantic_relation_ids/{index}"
        if relation is None:
            issues.append(_issue(
                "VP15-EXPRESSION-RELATION-MISSING",
                f"semantic relation is not frozen in the artifact: {relation_id}",
                relation_pointer,
            ))
        elif relation.relation_class not in accepted_classes:
            issues.append(_issue(
                "VP15-EXPRESSION-RELATION-KIND",
                f"{relation.relation_class} cannot satisfy {kind}",
                relation_pointer,
            ))
        elif feature_id not in relation.member_feature_ids:
            issues.append(_issue(
                "VP15-EXPRESSION-RELATION-MEMBER",
                f"semantic relation {relation_id} does not include feature {feature_id}",
                relation_pointer,
            ))


def _validate_spatial_direction(feature, requirement, relations, base, issues) -> None:
    expected = requirement["expected_spatial_direction_model"]
    if expected is None:
        return
    candidates = []
    if feature.axis is not None:
        candidates.append(feature.axis.direction)
    if feature.normal is not None:
        candidates.append(feature.normal)
    for relation_id in requirement["semantic_relation_ids"]:
        relation = relations.get(relation_id)
        if relation is None:
            continue
        relation_axis = getattr(relation, "axis", None)
        relation_normal = getattr(relation, "plane_normal", None)
        if relation_axis is not None:
            candidates.append(relation_axis.direction)
        if relation_normal is not None:
            candidates.append(relation_normal)
    if not candidates:
        issues.append(_issue(
            "VP15-EXPRESSION-SPATIAL-DIRECTION-UNPROVEN",
            "expected spatial direction has no frozen feature or relation direction evidence",
            base + "/expected_spatial_direction_model",
        ))
    elif not any(_parallel(expected, candidate) for candidate in candidates):
        issues.append(_issue(
            "VP15-EXPRESSION-SPATIAL-DIRECTION-MISMATCH",
            "expected spatial direction does not match frozen feature or relation evidence",
            base + "/expected_spatial_direction_model",
        ))


def _validate_section_preconditions(
    feature, requirement, targets, views, relations, base, issues
) -> None:
    if requirement["expression_method"] not in {"section_through_axis", "cross_section"}:
        return
    primary = next(
        (views.get(target["view_id"]) for target in targets if target["role"] == "primary"),
        None,
    )
    if primary is None or primary["type"] not in _SECTION_TYPES:
        return
    section = primary["section_definition"]
    if feature.axis is None:
        issues.append(_issue(
            "VP15-EXPRESSION-SECTION-AXIS-UNPROVEN",
            "section expression requires a frozen semantic feature axis",
            base + "/expression_method",
        ))
    elif not _axis_intersects_path(
        feature.axis.origin_m,
        feature.axis.direction,
        section["cutting_line_points_model_m"],
    ):
        issues.append(_issue(
            "VP15-EXPRESSION-SECTION-AXIS-MISS",
            "section cutting path does not intersect the frozen semantic feature axis",
            base + "/satisfied_by",
        ))
    if (
        feature.axial_extent is not None
        and float(section["section_depth_m"]) > 0
        and float(section["section_depth_m"])
        < float(feature.axial_extent.total_depth_m) - 1e-12
    ):
        issues.append(_issue(
            "VP15-EXPRESSION-SECTION-DEPTH",
            "bounded section depth does not cover the frozen semantic axial extent",
            base + "/satisfied_by",
        ))
    if primary["type"] == "half_section":
        relation_rows = [
            relations.get(relation_id) for relation_id in requirement["semantic_relation_ids"]
        ]
        if not any(
            relation is not None
            and relation.relation_class == "relation.symmetry_or_mirror"
            and feature.feature_id in relation.member_feature_ids
            for relation in relation_rows
        ):
            issues.append(_issue(
                "VP15-EXPRESSION-HALF-SECTION-SYMMETRY",
                "half-section expression requires a frozen symmetry or mirror relation",
                base + "/semantic_relation_ids",
            ))
    if feature.feature_class == "geometry.positive.rib" and feature.axis is not None:
        points = section["cutting_line_points_model_m"]
        segments = [
            tuple(float(right[i]) - float(left[i]) for i in range(3))
            for left, right in zip(points, points[1:])
        ]
        if any(_parallel(segment, feature.axis.direction) for segment in segments):
            issues.append(_issue(
                "VP15-EXPRESSION-RIB-LONGITUDINAL-SECTION",
                "a longitudinal rib/web cut cannot be treated as an ordinary hatched section",
                base + "/satisfied_by",
            ))


def _validate_discernibility(
    feature, requirement, targets, views, geometry_report, base, issues
) -> None:
    check = requirement["discernibility_check"]
    if check is None:
        return
    primary = next(
        (views.get(target["view_id"]) for target in targets if target["role"] == "primary"),
        None,
    )
    if primary is None or geometry_report is None:
        issues.append(_issue(
            "VP15-EXPRESSION-DISCERNIBILITY-UNPROVEN",
            "critical discernibility requires frozen geometry and a primary view",
            base + "/discernibility_check",
        ))
        return
    direction = _view_direction(primary, views)
    projected_size = _projected_feature_size(feature, geometry_report, direction)
    if projected_size is None:
        issues.append(_issue(
            "VP15-EXPRESSION-DISCERNIBILITY-UNPROVEN",
            "critical feature projected size cannot be derived from frozen geometry",
            base + "/discernibility_check",
        ))
        return
    size_sheet_m = projected_size * float(primary["scale"])
    threshold = (
        float(check["line_width_sheet_m"])
        * float(check["minimum_line_width_ratio"])
    )
    if size_sheet_m < threshold - 1e-12:
        issues.append(_issue(
            "VP15-EXPRESSION-DISCERNIBILITY",
            f"critical projected size {size_sheet_m:.12g} m is below {threshold:.12g} m",
            base + "/discernibility_check",
        ))


def _same_number(left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)


def _axis_intersects_path(origin, direction, points) -> bool:
    axis = _unit(direction)
    if axis is None or not isinstance(points, Sequence) or len(points) < 2:
        return False
    for start, end in zip(points, points[1:]):
        segment = tuple(float(end[index]) - float(start[index]) for index in range(3))
        relative = tuple(float(start[index]) - float(origin[index]) for index in range(3))
        a = sum(value * value for value in segment)
        if a <= 1e-24:
            continue
        b = sum(segment[index] * axis[index] for index in range(3))
        d = sum(segment[index] * relative[index] for index in range(3))
        e = sum(axis[index] * relative[index] for index in range(3))
        denominator = a - b * b
        if abs(denominator) <= 1e-18:
            parameter = min(1.0, max(0.0, -d / a))
        else:
            parameter = min(1.0, max(0.0, (b * e - d) / denominator))
        axis_parameter = b * parameter + e
        separation = tuple(
            relative[index] + parameter * segment[index] - axis_parameter * axis[index]
            for index in range(3)
        )
        segment_length = math.sqrt(a)
        if math.sqrt(sum(value * value for value in separation)) <= max(
            1e-9, segment_length * 1e-6
        ):
            return True
    return False


def _projected_feature_size(feature, geometry_report, view_direction):
    direction = _unit(view_direction)
    geometry_refs = getattr(feature, "geometry_refs", None)
    edge_ids = set(getattr(geometry_refs, "edge_ids", ()))
    if direction is None or not edge_ids:
        return None
    points = []
    for body in geometry_report.get("bodies", []):
        for edge in body.get("edges", []):
            if edge.get("id") not in edge_ids:
                continue
            for key in ("start_model_m", "end_model_m"):
                point = edge.get(key)
                if _unit_or_zero(point) is not None:
                    points.append(tuple(float(value) for value in point))
    if len(points) < 2:
        return None
    projected = []
    for point in points:
        depth = sum(point[index] * direction[index] for index in range(3))
        projected.append(
            tuple(point[index] - depth * direction[index] for index in range(3))
        )
    return max(
        math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))
        for left in projected
        for right in projected
    )


def _unit_or_zero(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        return None
    try:
        values = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(item) for item in values) else None


def _proves_true_shape(
    view: Mapping[str, Any],
    feature: SemanticFeature,
    views: Mapping[str, Mapping[str, Any]],
) -> bool:
    direction = _view_direction(view, views)
    if direction is None:
        return False
    candidates = []
    if feature.normal is not None:
        candidates.append(feature.normal)
    if feature.axis is not None:
        candidates.append(feature.axis.direction)
    return any(_parallel(direction, candidate) for candidate in candidates)


def _view_direction(
    view: Mapping[str, Any],
    views: Mapping[str, Mapping[str, Any]],
    visited: frozenset[str] = frozenset(),
):
    orientation = view.get("orientation")
    if not isinstance(orientation, Mapping):
        return None
    if orientation.get("kind") == "explicit_basis":
        return _unit(orientation.get("view_direction_model"))
    if orientation.get("kind") == "standard_model_view":
        return _STANDARD_DIRECTIONS.get(orientation.get("standard_view"))
    if orientation.get("kind") == "derived_from_parent":
        parent_id = view.get("parent_view_id")
        if not isinstance(parent_id, str) or parent_id in visited:
            return None
        parent = views.get(parent_id)
        if parent is None:
            return None
        current_id = view.get("id")
        next_visited = visited | ({current_id} if isinstance(current_id, str) else set())
        return _view_direction(parent, views, frozenset(next_visited))
    return None


def _independent_projection_count(directions: Sequence[Sequence[float]]) -> int:
    independent = []
    for direction in directions:
        unit = _unit(direction)
        if unit is not None and not any(_parallel(unit, current) for current in independent):
            independent.append(unit)
    return len(independent)


def _parallel(left, right) -> bool:
    left_unit = _unit(left)
    right_unit = _unit(right)
    if left_unit is None or right_unit is None:
        return False
    return abs(sum(a * b for a, b in zip(left_unit, right_unit))) >= 1.0 - 1e-8


def _unit(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        return None
    try:
        values = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(item) for item in values):
        return None
    length = math.sqrt(sum(item * item for item in values))
    if length <= 1e-12:
        return None
    return tuple(item / length for item in values)


def _issue(code: str, message: str, json_pointer: str) -> ValidationIssue:
    return validation_issue(code, "coverage", message, json_pointer)
