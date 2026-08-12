"""Plan-box, safe-zone, reserved-zone and dimension-band validation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from drawing_planner.planning_models import ValidationIssue
from drawing_planner.validators._common import (
    Rect,
    finite_number,
    point_in_rect,
    pointer,
    rect,
    rect_contains,
    rects_overlap,
    stable_issues,
    validation_issue,
)


_TOLERANCE = 1e-12


class ViewPlanLayoutValidator:
    def validate(self, plan: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        sheet = plan["sheet"]
        if not finite_number(sheet["width_m"]) or not finite_number(sheet["height_m"]):
            issues.append(
                validation_issue(
                    "VP-LAYOUT-SHEET",
                    "layout",
                    "sheet dimensions must be finite",
                    "/sheet",
                )
            )
            return stable_issues(issues)
        sheet_bounds: Rect = (0.0, 0.0, float(sheet["width_m"]), float(sheet["height_m"]))
        inner_bounds = self._validated_rect(
            plan["inner_frame"]["bounds_sheet_m"],
            "/inner_frame/bounds_sheet_m",
            "VP-LAYOUT-INNER-FRAME",
            issues,
        )
        safe_zone = self._validated_rect(
            plan["inner_frame"]["safe_zone_sheet_m"],
            "/inner_frame/safe_zone_sheet_m",
            "VP-LAYOUT-SAFE-ZONE",
            issues,
        )
        if inner_bounds is not None and not rect_contains(sheet_bounds, inner_bounds):
            issues.append(
                validation_issue(
                    "VP-LAYOUT-INNER-FRAME-SHEET",
                    "layout",
                    "inner-frame bounds must lie inside the sheet",
                    "/inner_frame/bounds_sheet_m",
                )
            )
        if safe_zone is not None and inner_bounds is not None:
            if not rect_contains(inner_bounds, safe_zone):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-SAFE-ZONE-INNER-FRAME",
                        "layout",
                        "safe zone must lie inside the inner-frame bounds",
                        "/inner_frame/safe_zone_sheet_m",
                    )
                )
            self._validate_frame_clearance(plan, inner_bounds, safe_zone, issues)

        reserved = self._validate_reserved_zones(plan, sheet_bounds, issues)
        placements = self._validate_placements(plan, safe_zone, reserved, issues)
        self._validate_local_profiles(plan, placements, issues)
        self._validate_placement_overlaps(placements, issues)
        dimensions = self._validate_dimension_zones(
            plan,
            safe_zone,
            placements,
            reserved,
            issues,
        )
        self._validate_labels(plan, sheet_bounds, safe_zone, reserved, dimensions, issues)
        return stable_issues(issues)

    @staticmethod
    def _validated_rect(value, value_pointer, code, issues) -> Rect | None:
        parsed = rect(value)
        if parsed is None:
            issues.append(
                validation_issue(
                    code,
                    "layout",
                    "rectangle coordinates must be finite and satisfy min < max",
                    value_pointer,
                )
            )
        return parsed

    @staticmethod
    def _validate_frame_clearance(plan, inner_bounds, safe_zone, issues) -> None:
        clearance = plan["clearance_policy"]["frame_clearance_m"]
        if not finite_number(clearance):
            issues.append(
                validation_issue(
                    "VP-LAYOUT-FRAME-CLEARANCE",
                    "layout",
                    "frame clearance must be finite",
                    "/clearance_policy/frame_clearance_m",
                )
            )
            return
        gaps = (
            safe_zone[0] - inner_bounds[0],
            safe_zone[1] - inner_bounds[1],
            inner_bounds[2] - safe_zone[2],
            inner_bounds[3] - safe_zone[3],
        )
        if any(gap + _TOLERANCE < float(clearance) for gap in gaps):
            issues.append(
                validation_issue(
                    "VP-LAYOUT-FRAME-CLEARANCE",
                    "layout",
                    "safe zone does not preserve clearance_policy.frame_clearance_m on every side",
                    "/inner_frame/safe_zone_sheet_m",
                )
            )

    def _validate_reserved_zones(self, plan, sheet_bounds, issues):
        rows: list[tuple[int, str, Rect]] = []
        ids = [zone["id"] for zone in plan["reserved_zones"]]
        counts = Counter(ids)
        for index, zone in enumerate(plan["reserved_zones"]):
            zone_pointer = pointer("reserved_zones", index)
            if counts[zone["id"]] > 1:
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DUPLICATE-RESERVED-ID",
                        "layout",
                        f"reserved-zone ID is duplicated: {zone['id']}",
                        zone_pointer + "/id",
                    )
                )
            bounds = self._validated_rect(
                zone["bounds_sheet_m"],
                zone_pointer + "/bounds_sheet_m",
                "VP-LAYOUT-RESERVED-RECT",
                issues,
            )
            if bounds is None:
                continue
            if not rect_contains(sheet_bounds, bounds):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-RESERVED-SHEET",
                        "layout",
                        "reserved zone must lie inside the sheet",
                        zone_pointer + "/bounds_sheet_m",
                    )
                )
            rows.append((index, zone["id"], bounds))
        return rows

    @staticmethod
    def _validate_local_profiles(plan, placements, issues) -> None:
        placement_by_id = {view_id: bounds for _, view_id, bounds in placements}
        view_by_id = {view["id"]: view for view in plan["views"]}
        for index, view in enumerate(plan["views"]):
            if view["type"] == "broken_out_section":
                source = view
                definition = view["broken_out_definition"]
                offset = definition["center_offset_from_view_m"]
                code = "VP-LAYOUT-BROKEN-OUT-PROFILE"
                definition_name = "broken_out_definition"
            elif view["type"] == "detail_view":
                source = view_by_id.get(view["parent_view_id"])
                definition = view["detail_definition"]
                offset = definition["center_offset_from_parent_m"]
                code = "VP-LAYOUT-DETAIL-PROFILE"
                definition_name = "detail_definition"
            else:
                continue
            source_bounds = (
                placement_by_id.get(source["id"]) if source is not None else None
            )
            radius = definition["radius_sheet_m"]
            if (
                source is None
                or source_bounds is None
                or not finite_number(radius)
                or not all(finite_number(value) for value in offset)
            ):
                continue
            center_x = float(source["position_sheet_m"][0]) + float(offset[0])
            center_y = float(source["position_sheet_m"][1]) + float(offset[1])
            radius_value = float(radius)
            if (
                center_x - radius_value < source_bounds[0] - _TOLERANCE
                or center_y - radius_value < source_bounds[1] - _TOLERANCE
                or center_x + radius_value > source_bounds[2] + _TOLERANCE
                or center_y + radius_value > source_bounds[3] + _TOLERANCE
            ):
                issues.append(
                    validation_issue(
                        code,
                        "layout",
                        "complete circular profile must lie inside the source view placement_box",
                        pointer("views", index, definition_name),
                    )
                )

    def _validate_placements(self, plan, safe_zone, reserved, issues):
        rows: list[tuple[int, str, Rect]] = []
        for index, view in enumerate(plan["views"]):
            view_pointer = pointer("views", index)
            bounds = self._validated_rect(
                view["placement_box"],
                view_pointer + "/placement_box",
                "VP-LAYOUT-PLACEMENT-RECT",
                issues,
            )
            if bounds is None:
                continue
            if safe_zone is not None and not rect_contains(safe_zone, bounds):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-PLACEMENT-SAFE-ZONE",
                        "layout",
                        "view placement_box must lie completely inside the safe zone",
                        view_pointer + "/placement_box",
                    )
                )
            if not point_in_rect(view["position_sheet_m"], bounds):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-VIEW-POSITION",
                        "layout",
                        "view insertion position must lie inside its placement_box",
                        view_pointer + "/position_sheet_m",
                    )
                )
            for _, reserved_id, reserved_bounds in reserved:
                if rects_overlap(bounds, reserved_bounds):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-PLACEMENT-RESERVED",
                            "layout",
                            f"view placement_box overlaps reserved zone {reserved_id}",
                            view_pointer + "/placement_box",
                        )
                    )
            rows.append((index, view["id"], bounds))
        return rows

    @staticmethod
    def _validate_placement_overlaps(placements, issues) -> None:
        for left_index in range(len(placements)):
            _, left_id, left_bounds = placements[left_index]
            for right_index in range(left_index + 1, len(placements)):
                source_index, right_id, right_bounds = placements[right_index]
                if rects_overlap(left_bounds, right_bounds):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-VIEW-OVERLAP",
                            "layout",
                            f"view placement boxes overlap: {left_id} and {right_id}",
                            pointer("views", source_index, "placement_box"),
                        )
                    )

    def _validate_dimension_zones(
        self,
        plan,
        safe_zone,
        placements,
        reserved,
        issues,
    ):
        placement_by_id = {view_id: bounds for _, view_id, bounds in placements}
        rows: list[tuple[int, str, Rect]] = []
        ids = [zone["id"] for zone in plan["dimension_zones"]]
        id_counts = Counter(ids)
        pairs = [(zone["view_id"], zone["side"]) for zone in plan["dimension_zones"]]
        pair_counts = Counter(pairs)
        policy = plan["clearance_policy"]
        for index, zone in enumerate(plan["dimension_zones"]):
            zone_pointer = pointer("dimension_zones", index)
            if id_counts[zone["id"]] > 1:
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DUPLICATE-DIMENSION-ID",
                        "layout",
                        f"dimension-zone ID is duplicated: {zone['id']}",
                        zone_pointer + "/id",
                    )
                )
            if pair_counts[(zone["view_id"], zone["side"])] > 1:
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DUPLICATE-DIMENSION-SIDE",
                        "layout",
                        f"view {zone['view_id']} repeats dimension side {zone['side']}",
                        zone_pointer + "/side",
                    )
                )
            bounds = self._validated_rect(
                zone["bounds_sheet_m"],
                zone_pointer + "/bounds_sheet_m",
                "VP-LAYOUT-DIMENSION-RECT",
                issues,
            )
            if bounds is None:
                continue
            if safe_zone is not None and not rect_contains(safe_zone, bounds):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DIMENSION-SAFE-ZONE",
                        "layout",
                        "dimension zone must lie completely inside the safe zone",
                        zone_pointer + "/bounds_sheet_m",
                    )
                )
            view_bounds = placement_by_id.get(zone["view_id"])
            if view_bounds is None:
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DIMENSION-VIEW",
                        "layout",
                        f"dimension zone references an unknown or invalid view: {zone['view_id']}",
                        zone_pointer + "/view_id",
                    )
                )
            else:
                self._validate_dimension_side(zone, bounds, view_bounds, zone_pointer, issues)
            required = zone["required_depth_m"]
            threshold = (
                policy["single_layer_dimension_depth_m"]
                if zone["dimension_layers"] == 1
                else policy["multi_layer_dimension_depth_m"]
            )
            if not finite_number(required) or not finite_number(threshold) or float(required) + _TOLERANCE < float(threshold):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DIMENSION-DEPTH-POLICY",
                        "layout",
                        "required_depth_m is below the 25/35 mm clearance policy",
                        zone_pointer + "/required_depth_m",
                    )
                )
            else:
                actual_depth = (
                    bounds[2] - bounds[0]
                    if zone["side"] in {"left", "right"}
                    else bounds[3] - bounds[1]
                )
                if actual_depth + _TOLERANCE < float(required):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-DIMENSION-DEPTH-BOUNDS",
                            "layout",
                            "dimension-zone bounds do not provide required_depth_m",
                            zone_pointer + "/bounds_sheet_m",
                        )
                    )
            for _, view_id, placement in placements:
                if rects_overlap(bounds, placement):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-DIMENSION-VIEW-OVERLAP",
                            "layout",
                            f"dimension zone overlaps view placement {view_id}",
                            zone_pointer + "/bounds_sheet_m",
                        )
                    )
            for _, reserved_id, reserved_bounds in reserved:
                if rects_overlap(bounds, reserved_bounds):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-DIMENSION-RESERVED",
                            "layout",
                            f"dimension zone overlaps reserved zone {reserved_id}",
                            zone_pointer + "/bounds_sheet_m",
                        )
                    )
            rows.append((index, zone["id"], bounds))

        for left_index in range(len(rows)):
            _, left_id, left_bounds = rows[left_index]
            for right_index in range(left_index + 1, len(rows)):
                source_index, right_id, right_bounds = rows[right_index]
                if rects_overlap(left_bounds, right_bounds):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-DIMENSION-OVERLAP",
                            "layout",
                            f"dimension zones overlap: {left_id} and {right_id}",
                            pointer("dimension_zones", source_index, "bounds_sheet_m"),
                        )
                    )
        return rows

    @staticmethod
    def _validate_dimension_side(zone, bounds, view_bounds, zone_pointer, issues) -> None:
        side = zone["side"]
        if side == "left":
            correctly_placed = bounds[2] <= view_bounds[0] + _TOLERANCE
            aligned_span = min(bounds[3], view_bounds[3]) - max(bounds[1], view_bounds[1])
        elif side == "right":
            correctly_placed = bounds[0] >= view_bounds[2] - _TOLERANCE
            aligned_span = min(bounds[3], view_bounds[3]) - max(bounds[1], view_bounds[1])
        elif side == "bottom":
            correctly_placed = bounds[3] <= view_bounds[1] + _TOLERANCE
            aligned_span = min(bounds[2], view_bounds[2]) - max(bounds[0], view_bounds[0])
        else:
            correctly_placed = bounds[1] >= view_bounds[3] - _TOLERANCE
            aligned_span = min(bounds[2], view_bounds[2]) - max(bounds[0], view_bounds[0])
        if not correctly_placed or aligned_span <= _TOLERANCE:
            issues.append(
                validation_issue(
                    "VP-LAYOUT-DIMENSION-SIDE",
                    "layout",
                    "dimension zone must lie on and span its declared side of the view",
                    zone_pointer + "/bounds_sheet_m",
                )
            )

    @staticmethod
    def _validate_labels(plan, sheet_bounds, safe_zone, reserved, dimensions, issues) -> None:
        shown = [
            view["label"]["text"]
            for view in plan["views"]
            if view["label"] is not None and view["label"]["show"]
        ]
        counts = Counter(shown)
        dimension_bounds = [bounds for _, _, bounds in dimensions]
        for index, view in enumerate(plan["views"]):
            label = view["label"]
            if label is None or not label["show"]:
                continue
            if counts[label["text"]] > 1:
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-DUPLICATE-LABEL",
                        "layout",
                        f"shown view label is duplicated: {label['text']}",
                        pointer("views", index, "label", "text"),
                    )
                )
            if label["position_mode"] != "explicit":
                continue
            position = label["position_sheet_m"]
            label_pointer = pointer("views", index, "label", "position_sheet_m")
            if not point_in_rect(position, sheet_bounds) or (
                safe_zone is not None and not point_in_rect(position, safe_zone)
            ):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-LABEL-SHEET",
                        "layout",
                        "explicit label position must lie inside the sheet safe zone",
                        label_pointer,
                    )
                )
                continue
            for _, reserved_id, reserved_bounds in reserved:
                if point_in_rect(position, reserved_bounds):
                    issues.append(
                        validation_issue(
                            "VP-LAYOUT-LABEL-RESERVED",
                            "layout",
                            f"explicit label position lies in reserved zone {reserved_id}",
                            label_pointer,
                        )
                    )
            if any(point_in_rect(position, bounds) for bounds in dimension_bounds):
                issues.append(
                    validation_issue(
                        "VP-LAYOUT-LABEL-DIMENSION",
                        "layout",
                        "explicit label position lies in a dimension zone",
                        label_pointer,
                    )
                )
