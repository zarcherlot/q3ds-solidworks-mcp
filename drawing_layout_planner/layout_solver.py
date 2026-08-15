"""Deterministic rule-plus-constraint solver and G3 layout gates."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .engine_models import (
    AnnotationLayoutIntent,
    DimensionLayoutIntent,
    LayoutIntents,
    LayoutValidationIssue,
    LeaderLayoutIntent,
    ScaleRatio,
    ViewLayoutIntent,
)
from .planning_models import LayoutAuthorization


RULESET_PATH = (
    Path(__file__).resolve().parent / "rulesets" / "deterministic-layout-v1.json"
)


@dataclass(frozen=True)
class Rect:
    left: float
    bottom: float
    right: float
    top: float

    @classmethod
    def from_value(cls, value: Sequence[float], label: str) -> "Rect":
        if len(value) != 4 or any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            for item in value
        ):
            raise ValueError(f"{label} must contain four finite numbers")
        rect = cls(*(float(item) for item in value))
        if rect.left > rect.right or rect.bottom > rect.top:
            raise ValueError(f"{label} must be normalized")
        return rect

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.bottom + self.top) / 2)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def moved_to(self, center: tuple[float, float]) -> "Rect":
        half_width = self.width / 2
        half_height = self.height / 2
        return Rect(
            center[0] - half_width,
            center[1] - half_height,
            center[0] + half_width,
            center[1] + half_height,
        )

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(
            self.left + dx,
            self.bottom + dy,
            self.right + dx,
            self.top + dy,
        )

    def scaled(self, center: tuple[float, float], factor: float) -> "Rect":
        return Rect(
            center[0] + (self.left - center[0]) * factor,
            center[1] + (self.bottom - center[1]) * factor,
            center[0] + (self.right - center[0]) * factor,
            center[1] + (self.top - center[1]) * factor,
        )


@dataclass
class ObjectState:
    object_id: str
    category: str
    view: str
    bounds: Rect
    exact: bool
    collision_usable: bool
    metadata: dict[str, Any]


@dataclass
class LayoutState:
    sheet_bounds: Rect
    safe_bounds: Rect
    objects: dict[str, ObjectState]
    locked_zones: dict[str, Rect]
    frozen_objects: set[str]
    view_positions: dict[str, tuple[float, float]]
    view_constraints: dict[str, dict[str, Any]]
    projection_alignments: tuple[dict[str, Any], ...]
    view_parentage: tuple[dict[str, Any], ...]
    minimum_spacing: dict[str, float]
    sheet_scale: tuple[int, int] | None
    dimension_segments: dict[str, tuple[tuple[float, float], ...]] = field(
        default_factory=dict
    )
    leader_routes: dict[str, tuple[tuple[float, float], ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class SolverOutcome:
    operations: tuple[dict[str, Any], ...]
    state: LayoutState
    issues: tuple[LayoutValidationIssue, ...]
    ruleset: Mapping[str, Any]
    ruleset_sha256: str


def load_ruleset(path: Path = RULESET_PATH) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("layout ruleset must contain an object")
    expected_phases = [
        "dimension_text_and_hierarchy",
        "leaders_and_labels",
        "movable_views",
        "local_scale",
        "sheet_scale",
        "authorized_sheet_format",
    ]
    if value.get("ruleset_id") != "deterministic-layout-v1" or value.get(
        "phase_order"
    ) != expected_phases:
        raise ValueError("layout ruleset ID or frozen phase order changed")
    operation_phase = value.get("operation_phase")
    expected_operations = {
        "set_dimension_hierarchy": 0,
        "move_dimension": 0,
        "move_annotation": 1,
        "route_leader": 1,
        "move_view": 2,
        "set_view_scale": 3,
        "set_sheet_scale": 4,
        "set_sheet_format": 5,
    }
    if operation_phase != expected_operations:
        raise ValueError("layout ruleset operation phases changed")
    if not isinstance(value.get("coordinate_precision_digits"), int) or not (
        3 <= value["coordinate_precision_digits"] <= 12
    ):
        raise ValueError("layout ruleset coordinate_precision_digits must be 3..12")
    if not isinstance(value.get("max_search_rings"), int) or not (
        0 <= value["max_search_rings"] <= 10000
    ):
        raise ValueError("layout ruleset max_search_rings must be 0..10000")
    for key in ("grid_m", "minimum_text_height_m", "minimum_arrow_size_m"):
        if (
            not isinstance(value.get(key), (int, float))
            or isinstance(value.get(key), bool)
            or not math.isfinite(float(value[key]))
            or value[key] <= 0
        ):
            raise ValueError(f"layout ruleset {key} must be finite and positive")
    return value, hashlib.sha256(raw).hexdigest()


class RepositoryLayoutSolver:
    """Resolve intent preferences to legal coordinates in a frozen phase order."""

    def __init__(self, ruleset_path: Path = RULESET_PATH):
        self.ruleset, self.ruleset_sha256 = load_ruleset(ruleset_path)
        self.grid = float(self.ruleset["grid_m"])
        self.precision = int(self.ruleset["coordinate_precision_digits"])
        self.max_rings = int(self.ruleset["max_search_rings"])

    def solve(
        self,
        handoff: Mapping[str, Any],
        intents: LayoutIntents,
        authorization: LayoutAuthorization,
    ) -> SolverOutcome:
        try:
            state = self._state_from_handoff(handoff)
        except ValueError as exc:
            empty = LayoutState(
                Rect(0, 0, 0, 0),
                Rect(0, 0, 0, 0),
                {},
                {},
                set(),
                {},
                {},
                (),
                (),
                {},
                None,
            )
            return SolverOutcome(
                (),
                empty,
                (self._issue("solver", "invalid-handoff-geometry", str(exc)),),
                self.ruleset,
                self.ruleset_sha256,
            )

        baseline_issue_keys = {
            self._issue_key(issue) for issue in self._baseline_geometry_issues(state)
        }
        operations: list[dict[str, Any]] = []
        issues: list[LayoutValidationIssue] = []

        for intent in self._ordered(intents.dimensions, "dimension_id"):
            self._place_dimension(state, intent, operations, issues)
        for intent in self._ordered(intents.annotations, "object_id"):
            self._place_annotation(state, intent, operations, issues)
        for intent in self._ordered(intents.leaders, "object_id"):
            self._route_leader(state, intent, operations, issues)
        for intent in self._ordered(intents.views, "view_name"):
            self._place_view(state, intent, authorization, operations, issues)
        for intent in self._ordered(intents.view_scales, "view_name"):
            self._set_view_scale(state, intent, authorization, operations, issues)
        if intents.sheet_scale is not None:
            self._set_sheet_scale(
                state, intents.sheet_scale.candidates, authorization, operations, issues
            )
        if intents.sheet_format is not None:
            self._set_sheet_format(
                state,
                intents.sheet_format.authorization_ids,
                authorization,
                operations,
                issues,
            )

        for sequence, operation in enumerate(operations):
            operation["sequence"] = sequence
        issues.extend(
            issue
            for issue in self.validate_final_state(state, operations)
            if self._issue_key(issue) not in baseline_issue_keys
        )
        return SolverOutcome(
            tuple(copy.deepcopy(operations)),
            state,
            tuple(_unique_issues(issues)),
            self.ruleset,
            self.ruleset_sha256,
        )

    def _state_from_handoff(self, handoff: Mapping[str, Any]) -> LayoutState:
        sheet = handoff["sheet"]
        objects: dict[str, ObjectState] = {}
        for row in handoff["objects"]:
            object_id = str(row["id"])
            metadata = {
                str(key): copy.deepcopy(value)
                for key, value in row.items()
                if key
                not in {
                    "id",
                    "category",
                    "view",
                    "bounds",
                    "exact",
                    "collision_usable",
                }
            }
            objects[object_id] = ObjectState(
                object_id,
                str(row["category"]),
                str(row["view"]),
                Rect.from_value(row["bounds"], f"object {object_id} bounds"),
                bool(row["exact"]),
                bool(row["collision_usable"]),
                metadata,
            )
        constraints = handoff["constraints"]
        raw_sheet_scale = (sheet.get("scale_numerator"), sheet.get("scale_denominator"))
        sheet_scale = (
            (int(raw_sheet_scale[0]), int(raw_sheet_scale[1]))
            if all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
                for value in raw_sheet_scale
            )
            else None
        )
        view_constraints: dict[str, dict[str, Any]] = {}
        view_positions: dict[str, tuple[float, float]] = {}
        for row in constraints["view_constraints"]:
            if not isinstance(row, Mapping) or not isinstance(row.get("view"), str):
                raise ValueError("view_constraints rows require a view name")
            position = _point(row.get("position_sheet_m"), "view position")
            view = str(row["view"])
            view_constraints[view] = dict(row)
            view_positions[view] = position
        return LayoutState(
            sheet_bounds=Rect.from_value(sheet["bounds_m"], "sheet bounds"),
            safe_bounds=Rect.from_value(sheet["safe_bounds_m"], "safe bounds"),
            objects=objects,
            locked_zones={
                str(row["zone_id"]): Rect.from_value(
                    row["bounds_m"], f"locked zone {row['zone_id']}"
                )
                for row in constraints["locked_zones"]
            },
            frozen_objects=set(constraints["frozen_objects"]),
            view_positions=view_positions,
            view_constraints=view_constraints,
            projection_alignments=tuple(
                dict(row) for row in constraints["projection_alignments"]
            ),
            view_parentage=tuple(dict(row) for row in constraints["view_parentage"]),
            minimum_spacing={
                str(key): float(value)
                for key, value in handoff["minimum_spacing_m"].items()
            },
            sheet_scale=sheet_scale,
        )

    @staticmethod
    def _ordered(values: Iterable[Any], identity: str) -> list[Any]:
        return sorted(values, key=lambda item: (-item.priority, str(getattr(item, identity))))

    def _place_dimension(
        self,
        state: LayoutState,
        intent: DimensionLayoutIntent,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        obj = self._target_object(
            state, intent.object_id, "dimension_display_bounds", "dimension", issues
        )
        if obj is None:
            return
        bound_dimension = obj.metadata.get("dimension_id")
        if bound_dimension != intent.dimension_id:
            issues.append(
                self._issue(
                    "solver",
                    "dimension-binding-mismatch",
                    "dimension intent does not match the handoff object binding",
                    intent.dimension_id,
                    intent.object_id,
                )
            )
            return
        attachment = obj.metadata.get("attachment_point_sheet_m")
        try:
            attachment_point = _point(
                attachment, "dimension attachment point"
            )
        except ValueError:
            issues.append(
                self._issue(
                    "dimension_crossing",
                    "dimension-attachment-missing",
                    "dimension placement requires one frozen attachment point for crossing checks",
                    intent.dimension_id,
                    intent.object_id,
                )
            )
            return
        try:
            current_position = _point(
                obj.metadata.get("current_position_sheet_m"),
                "dimension current position",
            )
        except ValueError:
            current_position = obj.bounds.center
        center = self._search_center(
            state, obj, intent.preferred_position_sheet_m, current_position
        )
        if center is None:
            issues.append(
                self._issue(
                    "solver",
                    "dimension-placement-unsatisfied",
                    "no deterministic legal position exists within the bounded search",
                    intent.dimension_id,
                    intent.object_id,
                )
            )
            return
        obj.bounds = obj.bounds.translated(
            center[0] - current_position[0], center[1] - current_position[1]
        )
        obj.metadata["current_position_sheet_m"] = center
        state.dimension_segments[intent.dimension_id] = (attachment_point, center)
        operations.append(
            {
                "operation_id": _operation_id(
                    "layout-dimension-hierarchy", intent.dimension_id
                ),
                "kind": "set_dimension_hierarchy",
                "dimension_id": intent.dimension_id,
                "tier": intent.tier,
                "stack_index": intent.stack_index,
            }
        )
        operations.append(
            {
                "operation_id": _operation_id("layout-move-dimension", intent.dimension_id),
                "kind": "move_dimension",
                "object_id": intent.object_id,
                "dimension_id": intent.dimension_id,
                "target_position_sheet_m": list(center),
                "preserve_attachment": True,
            }
        )

    def _place_annotation(
        self,
        state: LayoutState,
        intent: AnnotationLayoutIntent,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        obj = self._target_object(state, intent.object_id, None, "annotation", issues)
        if obj is None:
            return
        if obj.category not in {
            "note_text_bounds",
            "view_label_bounds",
            "section_symbol_bounds",
        }:
            issues.append(
                self._issue(
                    "solver",
                    "annotation-category-invalid",
                    f"object category {obj.category} is not a movable annotation",
                    intent.object_id,
                )
            )
            return
        try:
            current_position = _point(
                obj.metadata.get("current_position_sheet_m"),
                "annotation current position",
            )
        except ValueError:
            current_position = obj.bounds.center
        center = self._search_center(
            state, obj, intent.preferred_position_sheet_m, current_position
        )
        if center is None:
            issues.append(
                self._issue(
                    "solver",
                    "annotation-placement-unsatisfied",
                    "no deterministic legal position exists within the bounded search",
                    intent.object_id,
                )
            )
            return
        obj.bounds = obj.bounds.translated(
            center[0] - current_position[0], center[1] - current_position[1]
        )
        obj.metadata["current_position_sheet_m"] = center
        operations.append(
            {
                "operation_id": _operation_id("layout-move-annotation", intent.object_id),
                "kind": "move_annotation",
                "object_id": intent.object_id,
                "target_position_sheet_m": list(center),
            }
        )

    def _route_leader(
        self,
        state: LayoutState,
        intent: LeaderLayoutIntent,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        obj = self._target_object(state, intent.object_id, None, "leader", issues)
        if obj is None:
            return
        frozen_points = obj.metadata.get("leader_points_sheet_m")
        if isinstance(frozen_points, list) and len(frozen_points) >= 2:
            current = tuple(_point(point, "leader point") for point in frozen_points)
            if (
                _points_close(current[-1], intent.attachment_point_sheet_m)
                and _points_close(current[0], intent.preferred_end_sheet_m)
            ):
                # SolidWorks reports leader vertices from the annotation-side end to the
                # attached entity.  DrawingLayoutPlan freezes the engineering direction:
                # attachment point first, annotation-side end last.
                route = tuple(reversed(current))
                state.leader_routes[intent.object_id] = route
                operations.append(
                    {
                        "operation_id": _operation_id("layout-route-leader", intent.object_id),
                        "kind": "route_leader",
                        "object_id": intent.object_id,
                        "points_sheet_m": [list(point) for point in route],
                        "preserve_attachment": True,
                    }
                )
                return
        start = self._snap_point(intent.attachment_point_sheet_m)
        end = self._snap_point(intent.preferred_end_sheet_m)
        routes = (
            (start, (end[0], start[1]), end),
            (start, (start[0], end[1]), end),
            (start, end),
        )
        selected = next(
            (route for route in routes if self._route_is_feasible(state, obj, route)),
            None,
        )
        if selected is None:
            issues.append(
                self._issue(
                    "solver",
                    "leader-route-unsatisfied",
                    "no deterministic orthogonal leader route satisfies the constraints",
                    intent.object_id,
                )
            )
            return
        state.leader_routes[intent.object_id] = selected
        operations.append(
            {
                "operation_id": _operation_id("layout-route-leader", intent.object_id),
                "kind": "route_leader",
                "object_id": intent.object_id,
                "points_sheet_m": [list(point) for point in selected],
                "preserve_attachment": True,
            }
        )

    def _place_view(
        self,
        state: LayoutState,
        intent: ViewLayoutIntent,
        authorization: LayoutAuthorization,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        if intent.view_name not in authorization.movable_view_names:
            issues.append(
                self._issue(
                    "solver",
                    "view-move-unauthorized",
                    "view movement lacks explicit authorization",
                    intent.view_name,
                )
            )
            return
        row = state.view_constraints.get(intent.view_name)
        if row is None:
            issues.append(
                self._issue(
                    "solver",
                    "view-constraint-missing",
                    "view movement requires a frozen view constraint row",
                    intent.view_name,
                )
            )
            return
        outline = self._view_outline(state, intent.view_name)
        if outline is None:
            issues.append(
                self._issue(
                    "solver",
                    "view-outline-missing",
                    "movable views require one exact outline",
                    intent.view_name,
                )
            )
            return
        target = self._apply_alignment_target(
            state, intent.view_name, intent.preferred_position_sheet_m, issues
        )
        if target is None:
            return
        center = self._search_view_center(state, intent.view_name, outline, target)
        if center is None:
            issues.append(
                self._issue(
                    "solver",
                    "view-placement-unsatisfied",
                    "no deterministic legal view position exists within the bounded search",
                    intent.view_name,
                )
            )
            return
        old = state.view_positions[intent.view_name]
        dx, dy = center[0] - old[0], center[1] - old[1]
        for obj in state.objects.values():
            if obj.view == intent.view_name:
                obj.bounds = obj.bounds.translated(dx, dy)
        state.view_positions[intent.view_name] = center
        operations.append(
            {
                "operation_id": _operation_id("layout-move-view", intent.view_name),
                "kind": "move_view",
                "view_name": intent.view_name,
                "target_position_sheet_m": list(center),
                "preserve_alignment": True,
            }
        )

    def _set_view_scale(
        self,
        state: LayoutState,
        intent: Any,
        authorization: LayoutAuthorization,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        if intent.view_name not in authorization.scalable_view_names:
            issues.append(
                self._issue(
                    "solver",
                    "view-scale-unauthorized",
                    "local view scale lacks explicit authorization",
                    intent.view_name,
                )
            )
            return
        row = state.view_constraints.get(intent.view_name)
        if row is None or not all(
            isinstance(row.get(key), int)
            and not isinstance(row.get(key), bool)
            and row[key] > 0
            for key in ("scale_numerator", "scale_denominator")
        ):
            issues.append(
                self._issue(
                    "solver",
                    "view-scale-readback-missing",
                    "local scaling requires the frozen current view ratio",
                    intent.view_name,
                )
            )
            return
        current = row["scale_numerator"] / row["scale_denominator"]
        selected = next(
            (
                candidate
                for candidate in intent.candidates
                if abs(candidate.value - current) <= 1e-12
            ),
            None,
        )
        if selected is None:
            selected = self._select_view_scale(
                state, intent.view_name, current, intent.candidates
            )
        if selected is None:
            issues.append(
                self._issue(
                    "solver",
                    "view-scale-unsatisfied",
                    "none of the authorized local scale candidates is geometrically legal",
                    intent.view_name,
                )
            )
            return
        self._apply_view_scale(state, intent.view_name, selected.value / current)
        row["scale_numerator"] = selected.numerator
        row["scale_denominator"] = selected.denominator
        operations.append(
            {
                "operation_id": _operation_id("layout-scale-view", intent.view_name),
                "kind": "set_view_scale",
                "view_name": intent.view_name,
                "numerator": selected.numerator,
                "denominator": selected.denominator,
            }
        )

    def _set_sheet_scale(
        self,
        state: LayoutState,
        candidates: tuple[ScaleRatio, ...],
        authorization: LayoutAuthorization,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        if not authorization.allow_sheet_scale_change:
            issues.append(
                self._issue(
                    "solver",
                    "sheet-scale-unauthorized",
                    "sheet scale change lacks explicit authorization",
                )
            )
            return
        if state.sheet_scale is None:
            issues.append(
                self._issue(
                    "solver",
                    "sheet-scale-readback-missing",
                    "sheet scaling requires one positive frozen sheet ratio",
                )
            )
            return
        numerator, denominator = state.sheet_scale
        current = numerator / denominator
        selected: ScaleRatio | None = next(
            (
                candidate
                for candidate in candidates
                if abs(candidate.value - current) <= 1e-12
            ),
            None,
        )
        for candidate in candidates:
            if selected is not None:
                break
            provisional = copy.deepcopy(state)
            factor = candidate.value / current
            for view, row in provisional.view_constraints.items():
                if row.get("uses_sheet_scale") is True:
                    self._apply_view_scale(provisional, view, factor)
            if not self._basic_geometry_issues(provisional):
                selected = candidate
                break
        if selected is None:
            issues.append(
                self._issue(
                    "solver",
                    "sheet-scale-unsatisfied",
                    "none of the authorized sheet scale candidates is geometrically legal",
                )
            )
            return
        factor = selected.value / current
        for view, row in state.view_constraints.items():
            if row.get("uses_sheet_scale") is True:
                self._apply_view_scale(state, view, factor)
        state.sheet_scale = (selected.numerator, selected.denominator)
        operations.append(
            {
                "operation_id": "layout-set-sheet-scale",
                "kind": "set_sheet_scale",
                "numerator": selected.numerator,
                "denominator": selected.denominator,
            }
        )

    def _set_sheet_format(
        self,
        state: LayoutState,
        authorization_ids: tuple[str, ...],
        authorization: LayoutAuthorization,
        operations: list[dict[str, Any]],
        issues: list[LayoutValidationIssue],
    ) -> None:
        approved = {
            item.authorization_id: item for item in authorization.allowed_sheet_formats
        }
        unauthorized = [
            authorization_id
            for authorization_id in authorization_ids
            if authorization_id not in approved
        ]
        if len(unauthorized) == len(authorization_ids):
            issues.append(
                self._issue(
                    "solver",
                    "sheet-format-unauthorized",
                    "sheet format change lacks a listed approval",
                    *unauthorized,
                )
            )
            return
        selected = None
        selected_state = None
        for authorization_id in authorization_ids:
            item = approved.get(authorization_id)
            if item is None:
                continue
            provisional = copy.deepcopy(state)
            self._apply_sheet_format(provisional, item.width_m, item.height_m)
            unchanged = (
                abs(item.width_m - state.sheet_bounds.width) <= 1e-12
                and abs(item.height_m - state.sheet_bounds.height) <= 1e-12
            )
            if unchanged or not self._basic_geometry_issues(provisional):
                selected = item
                selected_state = provisional
                break
        if selected is None or selected_state is None:
            issues.append(
                self._issue(
                    "solver",
                    "sheet-format-unsatisfied",
                    "no listed approved sheet format satisfies the final geometry",
                    *authorization_ids,
                )
            )
            return
        state.sheet_bounds = selected_state.sheet_bounds
        state.safe_bounds = selected_state.safe_bounds
        state.locked_zones = selected_state.locked_zones
        operations.append(
            {
                "operation_id": _operation_id(
                    "layout-set-sheet-format", selected.authorization_id
                ),
                "kind": "set_sheet_format",
                "authorization_id": selected.authorization_id,
                "format_id": selected.format_id,
                "width_m": selected.width_m,
                "height_m": selected.height_m,
            }
        )

    def _target_object(
        self,
        state: LayoutState,
        object_id: str,
        expected_category: str | None,
        label: str,
        issues: list[LayoutValidationIssue],
    ) -> ObjectState | None:
        obj = state.objects.get(object_id)
        if obj is None:
            issues.append(
                self._issue(
                    "solver",
                    f"{label}-object-missing",
                    f"{label} intent references an unknown object",
                    object_id,
                )
            )
            return None
        if object_id in state.frozen_objects:
            issues.append(
                self._issue(
                    "solver",
                    f"{label}-object-frozen",
                    f"{label} intent targets a G1-frozen object",
                    object_id,
                )
            )
            return None
        if not obj.exact or not obj.collision_usable:
            issues.append(
                self._issue(
                    "solver",
                    f"{label}-boundary-inexact",
                    f"{label} placement requires an exact collision boundary",
                    object_id,
                )
            )
            return None
        if expected_category is not None and obj.category != expected_category:
            issues.append(
                self._issue(
                    "solver",
                    f"{label}-category-invalid",
                    f"expected {expected_category}, got {obj.category}",
                    object_id,
                )
            )
            return None
        return obj

    def _search_center(
        self,
        state: LayoutState,
        obj: ObjectState,
        preferred: tuple[float, float],
        current_position: Any = None,
    ) -> tuple[float, float] | None:
        try:
            current = _point(current_position, "current object position")
        except ValueError:
            current = obj.bounds.center
        if _points_close(current, preferred):
            return current
        for candidate in self._candidate_points(preferred):
            if self._round_point(current) == candidate:
                return candidate
            proposed = obj.bounds.translated(
                candidate[0] - current[0], candidate[1] - current[1]
            )
            if self._rect_is_feasible(state, obj.object_id, proposed, obj.category):
                return candidate
        return None

    def _search_view_center(
        self,
        state: LayoutState,
        view: str,
        outline: ObjectState,
        preferred: tuple[float, float],
    ) -> tuple[float, float] | None:
        old = state.view_positions[view]
        if _points_close(old, preferred):
            return old
        for candidate in self._candidate_points(preferred):
            if self._round_point(old) == candidate:
                return candidate
            dx, dy = candidate[0] - old[0], candidate[1] - old[1]
            provisional = copy.deepcopy(state)
            for obj in provisional.objects.values():
                if obj.view == view:
                    obj.bounds = obj.bounds.translated(dx, dy)
            provisional.view_positions[view] = candidate
            if not self._basic_geometry_issues(provisional):
                return candidate
        return None

    def _candidate_points(
        self, preferred: tuple[float, float]
    ) -> Iterable[tuple[float, float]]:
        base = self._snap_point(preferred)
        for ring in range(self.max_rings + 1):
            offsets = [
                (dx, dy)
                for dx in range(-ring, ring + 1)
                for dy in range(-ring, ring + 1)
                if max(abs(dx), abs(dy)) == ring
            ]
            offsets.sort(
                key=lambda pair: (
                    abs(pair[0]) + abs(pair[1]),
                    -pair[1],
                    abs(pair[0]),
                    pair[0],
                )
            )
            for dx, dy in offsets:
                yield self._round_point(
                    (base[0] + dx * self.grid, base[1] + dy * self.grid)
                )

    def _snap_point(self, point: tuple[float, float]) -> tuple[float, float]:
        return self._round_point(
            (round(point[0] / self.grid) * self.grid, round(point[1] / self.grid) * self.grid)
        )

    def _round_point(self, point: tuple[float, float]) -> tuple[float, float]:
        return (round(point[0], self.precision), round(point[1], self.precision))

    def _rect_is_feasible(
        self,
        state: LayoutState,
        object_id: str,
        proposed: Rect,
        category: str,
    ) -> bool:
        if not _contains(state.safe_bounds, proposed):
            return False
        if any(
            _gap(proposed, zone) < state.minimum_spacing["object_to_frame"]
            for zone in state.locked_zones.values()
        ):
            return False
        for other in state.objects.values():
            if (
                other.object_id == object_id
                or other.category == "sheet_border_bounds"
                or not other.collision_usable
            ):
                continue
            required = (
                state.minimum_spacing["text_to_geometry"]
                if _is_text(category) and other.category == "view_outline_bounds"
                else state.minimum_spacing["object_to_object"]
            )
            if _gap(proposed, other.bounds) < required:
                return False
        return True

    def _route_is_feasible(
        self,
        state: LayoutState,
        obj: ObjectState,
        route: tuple[tuple[float, float], ...],
    ) -> bool:
        if any(not _point_in_rect(state.safe_bounds, point) for point in route):
            return False
        for first, second in zip(route, route[1:]):
            for zone in state.locked_zones.values():
                if _segment_intersects_rect(first, second, zone):
                    return False
            for other in state.objects.values():
                if (
                    other.object_id == obj.object_id
                    or other.category == "sheet_border_bounds"
                    or other.view == obj.view
                ):
                    continue
                if _segment_intersects_rect(first, second, other.bounds):
                    return False
        return True

    def _apply_alignment_target(
        self,
        state: LayoutState,
        view: str,
        preferred: tuple[float, float],
        issues: list[LayoutValidationIssue],
    ) -> tuple[float, float] | None:
        x, y = preferred
        for row in state.projection_alignments:
            if row.get("view") != view:
                continue
            parent = row.get("parent_view")
            axis = row.get("axis")
            offset = row.get("offset_m", 0.0)
            if (
                not isinstance(parent, str)
                or parent not in state.view_positions
                or axis not in {"horizontal", "vertical"}
                or not isinstance(offset, (int, float))
                or isinstance(offset, bool)
            ):
                issues.append(
                    self._issue(
                        "projection_alignment",
                        "projection-constraint-invalid",
                        "projection alignment rows require view, parent_view, axis and finite offset_m",
                        view,
                    )
                )
                return None
            parent_position = state.view_positions[parent]
            if axis == "horizontal":
                y = parent_position[1] + float(offset)
            else:
                x = parent_position[0] + float(offset)
        return (x, y)

    def _select_view_scale(
        self,
        state: LayoutState,
        view: str,
        current: float,
        candidates: tuple[ScaleRatio, ...],
    ) -> ScaleRatio | None:
        for candidate in candidates:
            provisional = copy.deepcopy(state)
            self._apply_view_scale(provisional, view, candidate.value / current)
            if not self._basic_geometry_issues(provisional):
                return candidate
        return None

    @staticmethod
    def _apply_view_scale(state: LayoutState, view: str, factor: float) -> None:
        center = state.view_positions[view]
        for obj in state.objects.values():
            if obj.view != view:
                continue
            if obj.category == "view_outline_bounds":
                obj.bounds = obj.bounds.scaled(center, factor)
            else:
                old_center = obj.bounds.center
                new_center = (
                    center[0] + (old_center[0] - center[0]) * factor,
                    center[1] + (old_center[1] - center[1]) * factor,
                )
                obj.bounds = obj.bounds.moved_to(new_center)

    @staticmethod
    def _apply_sheet_format(state: LayoutState, width: float, height: float) -> None:
        old = state.sheet_bounds
        safe = state.safe_bounds
        left_margin = safe.left - old.left
        bottom_margin = safe.bottom - old.bottom
        right_margin = old.right - safe.right
        top_margin = old.top - safe.top
        state.sheet_bounds = Rect(old.left, old.bottom, old.left + width, old.bottom + height)
        state.safe_bounds = Rect(
            old.left + left_margin,
            old.bottom + bottom_margin,
            old.left + width - right_margin,
            old.bottom + height - top_margin,
        )
        dx, dy = width - old.width, height - old.height
        new_zones: dict[str, Rect] = {}
        for zone_id, zone in state.locked_zones.items():
            lowered = zone_id.lower()
            if "right" in lowered or "title" in lowered:
                zone = zone.translated(dx, 0)
            if "top" in lowered:
                zone = zone.translated(0, dy)
            new_zones[zone_id] = zone
        state.locked_zones = new_zones

    def _basic_geometry_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        return [
            *self._safe_area_issues(state),
            *self._locked_zone_issues(state),
            *self._collision_issues(state),
            *self._spacing_issues(state),
        ]

    def validate_final_state(
        self, state: LayoutState, operations: Sequence[Mapping[str, Any]]
    ) -> list[LayoutValidationIssue]:
        issues: list[LayoutValidationIssue] = []
        issues.extend(self._phase_order_issues(operations))
        issues.extend(self._safe_area_issues(state))
        issues.extend(self._locked_zone_issues(state))
        issues.extend(self._collision_issues(state))
        issues.extend(self._dimension_crossing_issues(state))
        issues.extend(self._projection_issues(state))
        issues.extend(self._spacing_issues(state))
        issues.extend(self._readability_issues(state))
        return issues

    def _baseline_geometry_issues(
        self, state: LayoutState
    ) -> list[LayoutValidationIssue]:
        return [
            *self._safe_area_issues(state),
            *self._locked_zone_issues(state),
            *self._collision_issues(state),
            *self._spacing_issues(state),
        ]

    @staticmethod
    def _issue_key(issue: LayoutValidationIssue) -> tuple[str, str, tuple[str, ...]]:
        return (issue.gate, issue.code, tuple(issue.references))

    def _phase_order_issues(
        self, operations: Sequence[Mapping[str, Any]]
    ) -> list[LayoutValidationIssue]:
        phases = self.ruleset["operation_phase"]
        values = [phases.get(operation.get("kind")) for operation in operations]
        if any(value is None for value in values) or values != sorted(values):
            return [
                self._issue(
                    "phase_order",
                    "phase-order-invalid",
                    "operations do not follow the frozen six-stage adjustment order",
                )
            ]
        return []

    def _safe_area_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        return [
            self._issue(
                "safe_area",
                "object-outside-safe-area",
                "a layout object extends outside the frozen safe area",
                obj.object_id,
            )
            for obj in state.objects.values()
            if obj.category != "sheet_border_bounds"
            and obj.collision_usable
            and not _contains(state.safe_bounds, obj.bounds)
        ]

    def _locked_zone_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        issues: list[LayoutValidationIssue] = []
        for obj in state.objects.values():
            if obj.category == "sheet_border_bounds" or not obj.collision_usable:
                continue
            for zone_id, zone in state.locked_zones.items():
                if _overlap_area(obj.bounds, zone) > 0:
                    issues.append(
                        self._issue(
                            "locked_zones",
                            "locked-zone-intrusion",
                            "a layout object intrudes into a frozen frame/title-block zone",
                            obj.object_id,
                            zone_id,
                        )
                    )
        return issues

    def _collision_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        objects = [
            item
            for item in state.objects.values()
            if item.category != "sheet_border_bounds" and item.collision_usable
        ]
        issues: list[LayoutValidationIssue] = []
        for index, first in enumerate(objects):
            for second in objects[index + 1 :]:
                if _overlap_area(first.bounds, second.bounds) > 0:
                    issues.append(
                        self._issue(
                            "collisions",
                            "positive-area-collision",
                            "two exact layout boundaries overlap with positive area",
                            first.object_id,
                            second.object_id,
                        )
                    )
        return issues

    def _dimension_crossing_issues(
        self, state: LayoutState
    ) -> list[LayoutValidationIssue]:
        issues: list[LayoutValidationIssue] = []
        dimension_view = {
            str(obj.metadata.get("dimension_id")): obj.view
            for obj in state.objects.values()
            if obj.category == "dimension_display_bounds"
        }
        outlines = [
            obj for obj in state.objects.values() if obj.category == "view_outline_bounds"
        ]
        for dimension_id, points in state.dimension_segments.items():
            for first, second in zip(points, points[1:]):
                for outline in outlines:
                    if outline.view == dimension_view.get(dimension_id):
                        continue
                    if _segment_intersects_rect(first, second, outline.bounds):
                        issues.append(
                            self._issue(
                                "dimension_crossing",
                                "dimension-crosses-unrelated-view",
                                "a solved dimension witness/text route crosses an unrelated view",
                                dimension_id,
                                outline.view,
                            )
                        )
        return issues

    def _projection_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        issues: list[LayoutValidationIssue] = []
        tolerance = 10 ** (-self.precision)
        graph: dict[str, str] = {}
        for row in state.view_parentage:
            child = row.get("view")
            parent = row.get("parent_view")
            if not isinstance(child, str) or not isinstance(parent, str):
                issues.append(
                    self._issue(
                        "projection_alignment",
                        "view-parentage-invalid",
                        "view parentage rows require view and parent_view",
                    )
                )
                continue
            graph[child] = parent
        for child in graph:
            seen: set[str] = set()
            node = child
            while node in graph:
                if node in seen:
                    issues.append(
                        self._issue(
                            "projection_alignment",
                            "view-parentage-cycle",
                            "view parentage must be acyclic",
                            child,
                        )
                    )
                    break
                seen.add(node)
                node = graph[node]
        for row in state.projection_alignments:
            view = row.get("view")
            parent = row.get("parent_view")
            axis = row.get("axis")
            offset = row.get("offset_m", 0.0)
            if (
                not isinstance(view, str)
                or not isinstance(parent, str)
                or view not in state.view_positions
                or parent not in state.view_positions
                or axis not in {"horizontal", "vertical"}
                or not isinstance(offset, (int, float))
                or isinstance(offset, bool)
            ):
                issues.append(
                    self._issue(
                        "projection_alignment",
                        "projection-constraint-invalid",
                        "projection alignment metadata is incomplete",
                        str(view),
                    )
                )
                continue
            coordinate = 1 if axis == "horizontal" else 0
            actual = state.view_positions[view][coordinate]
            expected = state.view_positions[parent][coordinate] + float(offset)
            if abs(actual - expected) > tolerance:
                issues.append(
                    self._issue(
                        "projection_alignment",
                        "projection-alignment-drift",
                        "a solved view violates its frozen projection alignment",
                        view,
                        parent,
                    )
                )
        return issues

    def _spacing_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        objects = [
            item
            for item in state.objects.values()
            if item.category != "sheet_border_bounds" and item.collision_usable
        ]
        issues: list[LayoutValidationIssue] = []
        for index, first in enumerate(objects):
            for second in objects[index + 1 :]:
                required = (
                    state.minimum_spacing["text_to_geometry"]
                    if (
                        _is_text(first.category)
                        and second.category == "view_outline_bounds"
                    )
                    or (
                        _is_text(second.category)
                        and first.category == "view_outline_bounds"
                    )
                    else state.minimum_spacing["object_to_object"]
                )
                actual = _gap(first.bounds, second.bounds)
                if actual < required:
                    issues.append(
                        self._issue(
                            "minimum_spacing",
                            "minimum-spacing-violation",
                            f"layout object gap {actual:.9f} m is below {required:.9f} m",
                            first.object_id,
                            second.object_id,
                        )
                    )
        return issues

    def _readability_issues(self, state: LayoutState) -> list[LayoutValidationIssue]:
        issues: list[LayoutValidationIssue] = []
        min_text = float(self.ruleset["minimum_text_height_m"])
        min_arrow = float(self.ruleset["minimum_arrow_size_m"])
        for obj in state.objects.values():
            if _is_text(obj.category):
                value = obj.metadata.get("text_height_m")
                if value is None:
                    value = min(obj.bounds.width, obj.bounds.height)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    or float(value) < min_text
                ):
                    issues.append(
                        self._issue(
                            "readability",
                            "text-height-unreadable",
                            "text height is missing, non-finite or below the frozen minimum",
                            obj.object_id,
                        )
                    )
        routed = set(state.leader_routes)
        for object_id in routed:
            obj = state.objects[object_id]
            value = obj.metadata.get("arrow_size_m")
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) < min_arrow
            ):
                issues.append(
                    self._issue(
                        "readability",
                        "arrow-size-unreadable",
                        "leader arrow size is missing, non-finite or below the frozen minimum",
                        object_id,
                    )
                )
        return issues

    def _view_outline(self, state: LayoutState, view: str) -> ObjectState | None:
        rows = [
            obj
            for obj in state.objects.values()
            if obj.view == view
            and obj.category == "view_outline_bounds"
            and obj.exact
            and obj.collision_usable
        ]
        return rows[0] if len(rows) == 1 else None

    @staticmethod
    def _issue(
        gate: str, code: str, message: str, *references: str
    ) -> LayoutValidationIssue:
        return LayoutValidationIssue(
            gate=gate,
            code=code,
            message=message,
            references=tuple(str(item) for item in references),
        )


def _point(value: Any, label: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            for item in value
        )
    ):
        raise ValueError(f"{label} must contain two finite numbers")
    return float(value[0]), float(value[1])


def _points_close(
    left: tuple[float, float], right: tuple[float, float], tolerance: float = 1e-12
) -> bool:
    return abs(left[0] - right[0]) <= tolerance and abs(left[1] - right[1]) <= tolerance


def _operation_token(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value)


def _operation_id(prefix: str, identity: str) -> str:
    candidate = f"{prefix}-{_operation_token(identity)}"
    if len(candidate) <= 256:
        return candidate
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _contains(outer: Rect, inner: Rect) -> bool:
    return (
        inner.left >= outer.left
        and inner.bottom >= outer.bottom
        and inner.right <= outer.right
        and inner.top <= outer.top
    )


def _point_in_rect(rect: Rect, point: tuple[float, float]) -> bool:
    return rect.left <= point[0] <= rect.right and rect.bottom <= point[1] <= rect.top


def _overlap_area(first: Rect, second: Rect) -> float:
    return max(0.0, min(first.right, second.right) - max(first.left, second.left)) * max(
        0.0, min(first.top, second.top) - max(first.bottom, second.bottom)
    )


def _gap(first: Rect, second: Rect) -> float:
    dx = max(second.left - first.right, first.left - second.right, 0.0)
    dy = max(second.bottom - first.top, first.bottom - second.top, 0.0)
    if dx == 0 and dy == 0:
        return 0.0
    return math.hypot(dx, dy)


def _segment_intersects_rect(
    first: tuple[float, float], second: tuple[float, float], rect: Rect
) -> bool:
    if _point_in_rect(rect, first) or _point_in_rect(rect, second):
        return True
    edges = (
        ((rect.left, rect.bottom), (rect.right, rect.bottom)),
        ((rect.right, rect.bottom), (rect.right, rect.top)),
        ((rect.right, rect.top), (rect.left, rect.top)),
        ((rect.left, rect.top), (rect.left, rect.bottom)),
    )
    return any(_segments_intersect(first, second, edge[0], edge[1]) for edge in edges)


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(
        p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (
            r[0] - p[0]
        )

    o1, o2 = orientation(a, b, c), orientation(a, b, d)
    o3, o4 = orientation(c, d, a), orientation(c, d, b)
    epsilon = 1e-12
    return (o1 * o2 < -epsilon) and (o3 * o4 < -epsilon)


def _is_text(category: str) -> bool:
    return category in {
        "dimension_display_bounds",
        "note_text_bounds",
        "view_label_bounds",
        "section_symbol_bounds",
    }


def _unique_issues(
    issues: Iterable[LayoutValidationIssue],
) -> list[LayoutValidationIssue]:
    result: list[LayoutValidationIssue] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for issue in issues:
        key = (issue.gate, issue.code, issue.references)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
