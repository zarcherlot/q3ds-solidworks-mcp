"""Cross-field, graph, evidence, orientation and frozen-geometry validation."""

from __future__ import annotations

import json
import math
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from drawing_planner.planning_models import ValidationIssue
from drawing_planner.validators._common import (
    finite_number,
    pointer,
    resolve_json_pointer,
    same_path,
    stable_issues,
    validation_issue,
)


_SECTION_TYPES = {
    "full_section",
    "half_section",
    "offset_section",
    "aligned_section",
    "removed_section",
}
_DERIVED_TYPES = _SECTION_TYPES | {"projected_view", "detail_view", "auxiliary_view"}
_TOLERANCE = 1e-9


class ViewPlanSemanticsValidator:
    def validate(
        self,
        plan: Mapping[str, Any],
        *,
        expected_producer: Mapping[str, str] | None = None,
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        self._validate_contract_identity(plan, expected_producer, issues)
        views = plan["views"]
        by_id: dict[str, Mapping[str, Any]] = {}
        for index, view in enumerate(views):
            view_id = view["id"]
            if view_id in by_id:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-DUPLICATE-VIEW-ID",
                        "semantics",
                        f"view id is duplicated: {view_id}",
                        pointer("views", index, "id"),
                    )
                )
            else:
                by_id[view_id] = view

        self._validate_main_view(plan, by_id, issues)
        self._validate_parent_graph(views, by_id, issues)
        self._validate_orientations(plan, views, issues)
        self._validate_projection_geometry(plan, views, by_id, issues)
        self._validate_sections(views, issues)
        self._validate_section_placement(views, by_id, issues)
        self._validate_center_elements(views, issues)
        self._validate_decisions(plan, by_id, issues)

        geometry = self._load_geometry(plan, issues)
        if geometry is not None:
            self._validate_evidence(plan, views, geometry, issues)
            self._validate_feature_references(plan, views, geometry, issues)
            self._validate_section_feature_axes(views, geometry, issues)
        return stable_issues(issues)

    @staticmethod
    def _validate_contract_identity(plan, expected_producer, issues) -> None:
        schema_reference = plan["$schema"]
        accepted_schema_id = "https://local.example/schemas/solidworks/view-plan/1.4"
        schema_name = schema_reference.replace("\\", "/").rsplit("/", 1)[-1]
        if schema_reference != accepted_schema_id and schema_name != "view-plan.schema.json":
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-SCHEMA-REFERENCE",
                    "semantics",
                    "$schema must reference the repository ViewPlan 1.4 contract",
                    "/$schema",
                )
            )
        if expected_producer is not None and dict(plan["producer"]) != dict(
            expected_producer
        ):
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-PRODUCER-TRUST",
                    "semantics",
                    "producer identity does not match the selected immutable planner profile",
                    "/producer",
                )
            )

    @staticmethod
    def _validate_main_view(plan, by_id, issues) -> None:
        main_id = plan["main_view_id"]
        main = by_id.get(main_id)
        if main is None:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-MAIN-VIEW-MISSING",
                    "semantics",
                    f"main_view_id does not reference a view: {main_id}",
                    "/main_view_id",
                )
            )
        elif main["type"] != "model_view":
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-MAIN-VIEW-TYPE",
                    "semantics",
                    "main_view_id must reference a model_view",
                    "/main_view_id",
                )
            )

    @staticmethod
    def _validate_parent_graph(views, by_id, issues) -> None:
        children: dict[str, list[str]] = {view_id: [] for view_id in by_id}
        indegree = {view_id: 0 for view_id in by_id}
        for index, view in enumerate(views):
            view_id = view["id"]
            parent_id = view["parent_view_id"]
            source = view["source"]
            if view["type"] in _DERIVED_TYPES:
                if parent_id not in by_id:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-PARENT-MISSING",
                            "semantics",
                            f"parent_view_id does not reference a view: {parent_id}",
                            pointer("views", index, "parent_view_id"),
                        )
                    )
                elif parent_id == view_id:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-PARENT-SELF",
                            "semantics",
                            "a view cannot be its own parent",
                            pointer("views", index, "parent_view_id"),
                        )
                    )
                else:
                    children[parent_id].append(view_id)
                    indegree[view_id] += 1
                if source.get("reference") != parent_id:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-SOURCE-PARENT-MISMATCH",
                            "semantics",
                            "source.reference must equal parent_view_id",
                            pointer("views", index, "source", "reference"),
                        )
                    )
            elif parent_id is not None:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-UNEXPECTED-PARENT",
                        "semantics",
                        f"{view['type']} cannot declare parent_view_id",
                        pointer("views", index, "parent_view_id"),
                    )
                )

        ready = deque(sorted(view_id for view_id, degree in indegree.items() if degree == 0))
        visited = 0
        while ready:
            current = ready.popleft()
            visited += 1
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(by_id):
            cyclic = sorted(view_id for view_id, degree in indegree.items() if degree > 0)
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-PARENT-CYCLE",
                    "semantics",
                    f"view parent graph contains a cycle: {', '.join(cyclic)}",
                    "/views",
                )
            )

    @staticmethod
    def _validate_orientations(plan, views, issues) -> None:
        explicit_allowed = (
            plan["execution_policy"]["transient_model_view_policy"]
            == "allow_in_memory_restore"
        )
        for index, view in enumerate(views):
            scale = view["scale"]
            if not finite_number(scale) or float(scale) <= 0:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-SCALE",
                        "semantics",
                        "view scale must be finite and positive",
                        pointer("views", index, "scale"),
                    )
                )
            orientation = view["orientation"]
            if not _finite_point2(view["position_sheet_m"]):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-VIEW-POSITION",
                        "semantics",
                        "view position must contain finite sheet coordinates",
                        pointer("views", index, "position_sheet_m"),
                    )
                )
            if "roll_angle_rad" in orientation and not finite_number(
                orientation["roll_angle_rad"]
            ):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-ROLL-ANGLE",
                        "semantics",
                        "orientation roll angle must be finite",
                        pointer("views", index, "orientation", "roll_angle_rad"),
                    )
                )
            if orientation["kind"] != "explicit_basis":
                continue
            if not explicit_allowed:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EXPLICIT-BASIS-POLICY",
                        "semantics",
                        "explicit_basis requires allow_in_memory_restore policy",
                        pointer("views", index, "orientation"),
                    )
                )
            view_vector = orientation["view_direction_model"]
            up_vector = orientation["up_direction_model"]
            if not _finite_vector(view_vector) or not _finite_vector(up_vector):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EXPLICIT-BASIS-FINITE",
                        "semantics",
                        "explicit basis vectors must contain finite numbers",
                        pointer("views", index, "orientation"),
                    )
                )
                continue
            view_norm = _norm(view_vector)
            up_norm = _norm(up_vector)
            if view_norm <= _TOLERANCE or up_norm <= _TOLERANCE:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EXPLICIT-BASIS-ZERO",
                        "semantics",
                        "explicit basis vectors must be non-zero",
                        pointer("views", index, "orientation"),
                    )
                )
            elif abs(_dot(view_vector, up_vector) / (view_norm * up_norm)) > 1e-6:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EXPLICIT-BASIS-ORTHOGONAL",
                        "semantics",
                        "explicit basis view and up directions must be orthogonal",
                        pointer("views", index, "orientation"),
                    )
                )

    @staticmethod
    def _validate_projection_geometry(plan, views, by_id, issues) -> None:
        projection_method = plan["projection_method"]
        for index, view in enumerate(views):
            if view["type"] != "projected_view":
                continue
            parent = by_id.get(view["parent_view_id"])
            if parent is None:
                continue
            direction = view["source"]["projection_direction"]
            alignment = view["alignment"]
            if direction in {"left", "right"} and alignment not in {
                "projected",
                "horizontal",
            }:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-ALIGNMENT",
                        "semantics",
                        f"{direction} projection requires horizontal alignment",
                        pointer("views", index, "alignment"),
                    )
                )
            if direction in {"up", "down"} and alignment not in {
                "projected",
                "vertical",
            }:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-ALIGNMENT",
                        "semantics",
                        f"{direction} projection requires vertical alignment",
                        pointer("views", index, "alignment"),
                    )
                )
            child_position = view["position_sheet_m"]
            parent_position = parent["position_sheet_m"]
            if not _finite_point2(child_position) or not _finite_point2(parent_position):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-POSITION",
                        "semantics",
                        "projected-view positions must contain finite numbers",
                        pointer("views", index, "position_sheet_m"),
                    )
                )
                continue
            expected_sign = 1 if direction in {"right", "up"} else -1
            if projection_method == "first_angle":
                expected_sign *= -1
            delta = (
                child_position[0] - parent_position[0]
                if direction in {"left", "right"}
                else child_position[1] - parent_position[1]
            )
            cross_delta = (
                child_position[1] - parent_position[1]
                if direction in {"left", "right"}
                else child_position[0] - parent_position[0]
            )
            if delta * expected_sign <= _TOLERANCE:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-METHOD",
                        "semantics",
                        f"projected-view position conflicts with {projection_method}",
                        pointer("views", index, "position_sheet_m"),
                    )
                )
            if abs(cross_delta) > _TOLERANCE:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-CROSS-AXIS",
                        "semantics",
                        "projected view and parent must share the aligned sheet coordinate",
                        pointer("views", index, "position_sheet_m"),
                    )
                )
            if not math.isclose(
                float(view["scale"]), float(parent["scale"]), rel_tol=1e-9, abs_tol=1e-12
            ):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-PROJECTION-SCALE",
                        "semantics",
                        "projected view must inherit its parent scale",
                        pointer("views", index, "scale"),
                    )
                )

    @staticmethod
    def _validate_sections(views, issues) -> None:
        for index, view in enumerate(views):
            view_type = view["type"]
            section = view["section_definition"]
            if view_type == "broken_out_section":
                definition = view["broken_out_definition"]
                if (
                    not _finite_point2(definition["center_offset_from_view_m"])
                    or not finite_number(definition["radius_sheet_m"])
                    or not finite_number(definition["depth_m"])
                ):
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-BROKEN-OUT-GEOMETRY",
                            "semantics",
                            "broken-out boundary and depth must contain finite values",
                            pointer("views", index, "broken_out_definition"),
                        )
                    )
                continue
            if view_type == "detail_view":
                definition = view["detail_definition"]
                if (
                    not _finite_point2(definition["center_offset_from_parent_m"])
                    or not finite_number(definition["radius_sheet_m"])
                ):
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-DETAIL-GEOMETRY",
                            "detail boundary must contain finite values",
                            pointer("views", index, "detail_definition"),
                        )
                    )
                continue
            if view_type == "auxiliary_view":
                definition = view["auxiliary_definition"]
                start = definition["reference_edge_start_model_m"]
                end = definition["reference_edge_end_model_m"]
                if not _finite_vector(start) or not _finite_vector(end) or _norm(
                    _subtract(end, start)
                ) <= _TOLERANCE:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-AUXILIARY-EDGE",
                            "semantics",
                            "auxiliary reference-edge endpoints must be finite and distinct",
                            pointer("views", index, "auxiliary_definition"),
                        )
                    )
                continue
            if view_type in {"model_view", "projected_view"}:
                continue
            points = section["cutting_line_points_model_m"]
            base_pointer = pointer("views", index, "section_definition", "cutting_line_points_model_m")
            if not finite_number(section["section_depth_m"]) or (
                section["line_extension_ratio"] is not None
                and not finite_number(section["line_extension_ratio"])
            ):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-SECTION-FINITE",
                        "semantics",
                        "section depth and extension ratio must be finite",
                        pointer("views", index, "section_definition"),
                    )
                )
            if not all(_finite_vector(point) for point in points):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-SECTION-FINITE",
                        "semantics",
                        "section cutting-line points must contain finite coordinates",
                        base_pointer,
                    )
                )
                continue
            segments = [_subtract(points[item + 1], points[item]) for item in range(len(points) - 1)]
            if any(_norm(segment) <= _TOLERANCE for segment in segments):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-SECTION-ZERO-SEGMENT",
                        "semantics",
                        "section cutting paths cannot contain zero-length segments",
                        base_pointer,
                    )
                )
                continue
            if view_type == "half_section":
                cosine = abs(_dot(segments[0], segments[1]) / (_norm(segments[0]) * _norm(segments[1])))
                if cosine > 1e-6:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-HALF-SECTION-PERPENDICULAR",
                            "semantics",
                            "half-section cutting segments must be perpendicular",
                            base_pointer,
                        )
                    )
            elif view_type == "aligned_section":
                cosine = abs(_dot(segments[0], segments[1]) / (_norm(segments[0]) * _norm(segments[1])))
                if math.isclose(cosine, 1.0, rel_tol=0.0, abs_tol=1e-6):
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-ALIGNED-SECTION-COLLINEAR",
                            "semantics",
                            "aligned-section cutting segments must not be collinear",
                            base_pointer,
                        )
                    )

    @staticmethod
    def _validate_section_placement(views, by_id, issues) -> None:
        for index, view in enumerate(views):
            if view["type"] != "full_section" or view["alignment"] != "projected":
                continue
            parent = by_id.get(view["parent_view_id"])
            if parent is None:
                continue
            child_position = view["position_sheet_m"]
            parent_position = parent["position_sheet_m"]
            if not _finite_point2(child_position) or not _finite_point2(parent_position):
                continue
            horizontal = view["section_definition"]["cutting_line_axis"] == "horizontal"
            aligned_delta = (
                child_position[0] - parent_position[0]
                if horizontal
                else child_position[1] - parent_position[1]
            )
            projected_delta = (
                child_position[1] - parent_position[1]
                if horizontal
                else child_position[0] - parent_position[0]
            )
            if abs(aligned_delta) > _TOLERANCE or abs(projected_delta) <= _TOLERANCE:
                relationship = "X" if horizontal else "Y"
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-SECTION-PLACEMENT",
                        "semantics",
                        f"projected full section must share parent {relationship} coordinate",
                        pointer("views", index, "position_sheet_m"),
                    )
                )
    @staticmethod
    def _validate_center_elements(views, issues) -> None:
        center_ids: list[tuple[str, str]] = []
        centerline_ids: list[tuple[str, str]] = []
        default_show_lines: set[bool] = set()
        for view_index, view in enumerate(views):
            for mark_index, mark in enumerate(view["center_marks"]):
                center_ids.append((mark["id"], pointer("views", view_index, "center_marks", mark_index, "id")))
                if mark["use_document_defaults"]:
                    default_show_lines.add(mark["show_lines"])
                if mark["style"] == "circular_group" and not mark["show_lines"]:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-CIRCULAR-CENTER-MARK-LINES",
                            "semantics",
                            "circular_group center marks require show_lines=true",
                            pointer("views", view_index, "center_marks", mark_index, "show_lines"),
                        )
                    )
            for line_index, centerline in enumerate(view["symmetry_centerlines"]):
                centerline_ids.append(
                    (
                        centerline["id"],
                        pointer("views", view_index, "symmetry_centerlines", line_index, "id"),
                    )
                )
        _append_duplicate_issues(center_ids, "VP-SEMANTICS-DUPLICATE-CENTER-MARK-ID", "center-mark", issues)
        _append_duplicate_issues(
            centerline_ids,
            "VP-SEMANTICS-DUPLICATE-CENTERLINE-ID",
            "symmetry-centerline",
            issues,
        )
        if len(default_show_lines) > 1:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-CENTER-MARK-DEFAULT-CONFLICT",
                    "semantics",
                    "all center marks using document defaults must request one show_lines value",
                    "/views",
                )
            )

    @staticmethod
    def _validate_decisions(plan, by_id, issues) -> None:
        if plan["open_questions"]:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-OPEN-QUESTIONS",
                    "semantics",
                    "top-level open_questions must be empty",
                    "/open_questions",
                )
            )
        summary = plan["decision_summary"]
        if summary["open_questions"]:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-DECISION-OPEN-QUESTIONS",
                    "semantics",
                    "decision_summary.open_questions must be empty",
                    "/decision_summary/open_questions",
                )
            )
        selected = [row for row in summary["main_orientation_comparison"] if row["selected"]]
        if len(selected) != 1:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-MAIN-ORIENTATION-SELECTION",
                    "semantics",
                    "main_orientation_comparison must select exactly one candidate",
                    "/decision_summary/main_orientation_comparison",
                )
            )
        final_ids = [row["view_id"] for row in summary["final_minimum_view_set"]]
        if len(final_ids) != len(set(final_ids)):
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-MINIMUM-SET-DUPLICATE",
                    "semantics",
                    "final_minimum_view_set cannot repeat view IDs",
                    "/decision_summary/final_minimum_view_set",
                )
            )
        if set(final_ids) != set(by_id):
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-MINIMUM-SET-MISMATCH",
                    "semantics",
                    "final_minimum_view_set must enumerate every planned view exactly once",
                    "/decision_summary/final_minimum_view_set",
                )
            )

    @staticmethod
    def _load_geometry(plan, issues) -> Any | None:
        path = Path(plan["geometry_report_path"])
        try:
            return json.loads(
                path.read_text(encoding="utf-8-sig"),
                object_pairs_hook=_unique_object,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            issues.append(
                validation_issue(
                    "VP-SEMANTICS-GEOMETRY-REPORT",
                    "semantics",
                    f"geometry report is not a unique-key JSON document: {exc}",
                    "/geometry_report_path",
                )
            )
            return None

    @staticmethod
    def _validate_evidence(plan, views, geometry, issues) -> None:
        expected_path = plan["geometry_report_path"]
        for evidence, evidence_pointer in _evidence_rows(views):
            if not same_path(evidence["report_path"], expected_path):
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EVIDENCE-REPORT",
                        "semantics",
                        "model evidence must reference the frozen geometry report",
                        evidence_pointer + "/report_path",
                    )
                )
            found, _ = resolve_json_pointer(geometry, evidence["json_pointer"])
            if not found:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-EVIDENCE-POINTER",
                        "semantics",
                        f"geometry evidence JSON Pointer does not resolve: {evidence['json_pointer']}",
                        evidence_pointer + "/json_pointer",
                    )
                )

    @staticmethod
    def _validate_feature_references(plan, views, geometry, issues) -> None:
        known = _collect_geometry_ids(geometry)
        references: list[tuple[str, str]] = []
        for view_index, view in enumerate(views):
            for feature_index, feature_id in enumerate(view["expressed_features"]):
                references.append(
                    (feature_id, pointer("views", view_index, "expressed_features", feature_index))
                )
            for mark_index, mark in enumerate(view["center_marks"]):
                for feature_index, feature_id in enumerate(mark["feature_ids"]):
                    references.append(
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
                    references.append(
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
        for feature_index, coverage in enumerate(plan["feature_coverage"]):
            references.append(
                (coverage["feature_id"], pointer("feature_coverage", feature_index, "feature_id"))
            )
        for feature_id, feature_pointer in references:
            if feature_id not in known:
                issues.append(
                    validation_issue(
                        "VP-SEMANTICS-FEATURE-EVIDENCE",
                        "semantics",
                        f"feature ID is not present in the frozen geometry report: {feature_id}",
                        feature_pointer,
                    )
                )

    @staticmethod
    def _validate_section_feature_axes(views, geometry, issues) -> None:
        for view_index, view in enumerate(views):
            if view["type"] not in {"full_section", "offset_section"}:
                continue
            section = view["section_definition"]
            segments = [
                (points_index, points_index + 1)
                for points_index in range(
                    max(0, len(section["cutting_line_points_model_m"]) - 1)
                )
            ]
            points = section["cutting_line_points_model_m"]
            for feature_index, feature_id in enumerate(section["feature_ids"]):
                feature = _find_geometry_feature(geometry, feature_id)
                axis = _axis_data(feature)
                feature_pointer = pointer(
                    "views",
                    view_index,
                    "section_definition",
                    "feature_ids",
                    feature_index,
                )
                if axis is None:
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-SECTION-FEATURE-AXIS",
                            "semantics",
                            f"section feature lacks a finite axis origin and direction: {feature_id}",
                            feature_pointer,
                        )
                    )
                    continue
                if view["type"] != "offset_section":
                    continue
                origin, _ = axis
                if not any(
                    _point_on_segment(origin, points[start], points[end])
                    for start, end in segments
                ):
                    issues.append(
                        validation_issue(
                            "VP-SEMANTICS-OFFSET-FEATURE-AXIS",
                            "semantics",
                            f"offset-section feature axis does not intersect a finite cutting segment: {feature_id}",
                            feature_pointer,
                        )
                    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _collect_geometry_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"id", "feature_id"} and isinstance(child, str):
                result.add(child)
            result.update(_collect_geometry_ids(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            result.update(_collect_geometry_ids(child))
    return result


def _find_geometry_feature(value: Any, feature_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if value.get("id") == feature_id or value.get("feature_id") == feature_id:
            return value
        for child in value.values():
            found = _find_geometry_feature(child, feature_id)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found = _find_geometry_feature(child, feature_id)
            if found is not None:
                return found
    return None


def _axis_data(feature: Mapping[str, Any] | None):
    if feature is None:
        return None
    candidates = [feature]
    for key in ("surface_parameters", "axis", "geometry"):
        child = feature.get(key)
        if isinstance(child, Mapping):
            candidates.append(child)
    origin = None
    direction = None
    for candidate in candidates:
        for key in (
            "origin",
            "center",
            "point_on_axis",
            "axis_origin",
            "origin_model_m",
            "center_model_m",
        ):
            value = candidate.get(key)
            if _finite_vector(value):
                origin = value
                break
        for key in ("axis", "direction", "axis_direction", "direction_model"):
            value = candidate.get(key)
            if _finite_vector(value) and _norm(value) > _TOLERANCE:
                direction = value
                break
        if origin is not None and direction is not None:
            return origin, direction
    return None


def _point_on_segment(point_value, start, end) -> bool:
    segment = _subtract(end, start)
    length_squared = _dot(segment, segment)
    if length_squared <= _TOLERANCE * _TOLERANCE:
        return False
    relative = _subtract(point_value, start)
    parameter = _dot(relative, segment) / length_squared
    if parameter < -1e-8 or parameter > 1.0 + 1e-8:
        return False
    closest = tuple(float(start[index]) + parameter * segment[index] for index in range(3))
    distance = _norm(_subtract(point_value, closest))
    return distance <= max(1e-9, math.sqrt(length_squared) * 1e-6)


def _evidence_rows(views):
    for view_index, view in enumerate(views):
        for evidence_index, evidence in enumerate(view["model_evidence"]):
            yield evidence, pointer("views", view_index, "model_evidence", evidence_index)
        for line_index, centerline in enumerate(view["symmetry_centerlines"]):
            for evidence_index, evidence in enumerate(centerline["model_evidence"]):
                yield evidence, pointer(
                    "views",
                    view_index,
                    "symmetry_centerlines",
                    line_index,
                    "model_evidence",
                    evidence_index,
                )


def _append_duplicate_issues(rows, code, label, issues) -> None:
    counts = Counter(value for value, _ in rows)
    for value, value_pointer in rows:
        if counts[value] > 1:
            issues.append(
                validation_issue(
                    code,
                    "semantics",
                    f"{label} id is duplicated: {value}",
                    value_pointer,
                )
            )


def _finite_vector(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) == 3
        and all(finite_number(item) for item in value)
    )


def _finite_point2(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) == 2
        and all(finite_number(item) for item in value)
    )


def _subtract(left, right):
    return tuple(float(a) - float(b) for a, b in zip(left, right, strict=True))


def _dot(left, right) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def _norm(value) -> float:
    return math.sqrt(_dot(value, value))
