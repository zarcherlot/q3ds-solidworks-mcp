"""Source, attachment, semantic, coverage, redundancy and layout gates."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from dimension_planner.planning_models import (
    DimensionValidationIssue,
    dimension_plan_from_mapping,
)
from ._common import (
    by_id,
    contains,
    issue,
    overlaps,
    point,
    pointer,
    rect,
    rect_contains,
    stable_issues,
)


_VALUE_TOLERANCE = 1e-12
_TWO_ATTACHMENT_KINDS = {
    "linear",
    "aligned",
    "angular",
    "hole_spacing",
    "hole_group_location",
    "overall",
    "step",
    "slot",
    "symmetric",
}


class DimensionSourceValidator:
    def validate(
        self,
        plan: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> tuple[DimensionValidationIssue, ...]:
        issues: list[DimensionValidationIssue] = []
        collections = {
            "model_driven_dimensions": by_id(
                handoff["model_driven_dimensions"], "dimension_id"
            ),
            "pmi_annotations": by_id(handoff["pmi_annotations"], "annotation_id"),
            "manufacturing_features": by_id(
                handoff["manufacturing_features"], "feature_id"
            ),
        }
        approved = by_id(handoff["approved_user_inputs"], "input_id")
        measurements = by_id(handoff["reference_measurements"], "measurement_id")
        for rows, key, label in (
            (handoff["model_driven_dimensions"], "dimension_id", "model dimension"),
            (handoff["pmi_annotations"], "annotation_id", "PMI annotation"),
            (handoff["manufacturing_features"], "feature_id", "manufacturing feature"),
            (handoff["approved_user_inputs"], "input_id", "approved input"),
            (handoff["reference_measurements"], "measurement_id", "measurement"),
        ):
            for duplicate in _duplicates(rows, key):
                issues.append(
                    issue(
                        "DP-SOURCE-ID-AMBIGUOUS",
                        "source",
                        f"frozen {label} ID is duplicated: {duplicate}",
                        "/" + key,
                    )
                )

        for index, dimension in enumerate(plan["dimensions"]):
            base = pointer("dimensions", index)
            source = dimension["source"]
            tier = source["source_tier"]
            nominal = float(dimension["value"]["nominal_si"])
            if tier == "model_or_pmi":
                collection_name = source["handoff_collection"]
                collection = collections[collection_name]
                rows = self._require_ids(
                    source["source_ids"],
                    collection,
                    base + "/source/source_ids",
                    issues,
                )
                values = [row.get("value_si") for row in rows]
                if not any(_close(value, nominal) for value in values):
                    issues.append(
                        issue(
                            "DP-SOURCE-NOMINAL-UNBOUND",
                            "source",
                            "nominal value is not present in the declared model/PMI source",
                            base + "/value/nominal_si",
                        )
                    )
                if dimension["tolerance"] is not None:
                    issues.append(
                        issue(
                            "DP-SOURCE-TOLERANCE-UNTRUSTED",
                            "source",
                            "model/PMI handoff contains no trusted tolerance payload",
                            base + "/tolerance",
                        )
                    )
            elif tier == "user_confirmed_input":
                rows = self._require_ids(
                    source["approved_input_ids"],
                    approved,
                    base + "/source/approved_input_ids",
                    issues,
                )
                quantity_values = [
                    row["value"]["value_si"]
                    for row in rows
                    if row.get("value", {}).get("kind") == "quantity"
                ]
                if not any(_close(value, nominal) for value in quantity_values):
                    issues.append(
                        issue(
                            "DP-SOURCE-APPROVED-NOMINAL",
                            "source",
                            "nominal value is not present in the approved inputs",
                            base + "/value/nominal_si",
                        )
                    )
                quantity_kind = dimension["value"]["quantity_kind"]
                if not any(
                    row.get("value", {}).get("kind") == "quantity"
                    and row["value"].get("quantity_kind") == quantity_kind
                    and _close(row["value"].get("value_si"), nominal)
                    for row in rows
                ):
                    issues.append(
                        issue(
                            "DP-SOURCE-APPROVED-QUANTITY-KIND",
                            "source",
                            "approved nominal uses a different quantity kind",
                            base + "/value/quantity_kind",
                        )
                    )
                dimension_features = set(dimension["feature_ids"])
                for row in rows:
                    if not set(row["target_feature_ids"]).issubset(dimension_features):
                        issues.append(
                            issue(
                                "DP-SOURCE-APPROVED-FEATURE",
                                "source",
                                "approved input targets features outside the dimension binding",
                                base + "/feature_ids",
                            )
                        )
                self._validate_approved_tolerance(
                    dimension["tolerance"], rows, base, issues
                )
            else:
                rows = self._require_ids(
                    source["measurement_ids"],
                    measurements,
                    base + "/source/measurement_ids",
                    issues,
                )
                if not any(_close(row.get("value_si"), nominal) for row in rows):
                    issues.append(
                        issue(
                            "DP-SOURCE-REFERENCE-NOMINAL",
                            "source",
                            "reference nominal does not match the frozen measurement",
                            base + "/value/nominal_si",
                        )
                    )
        return stable_issues(issues)

    @staticmethod
    def _require_ids(ids, collection, value_pointer, issues):
        rows = []
        for source_id in ids:
            row = collection.get(source_id)
            if row is None:
                issues.append(
                    issue(
                        "DP-SOURCE-ID-MISSING",
                        "source",
                        f"source ID is absent from the frozen handoff: {source_id}",
                        value_pointer,
                    )
                )
            else:
                rows.append(row)
        return rows

    @staticmethod
    def _validate_approved_tolerance(tolerance, rows, base, issues):
        if tolerance is None:
            return
        if tolerance["kind"] == "fit":
            texts = {
                row["value"]["text"]
                for row in rows
                if row.get("value", {}).get("kind") == "exact_text"
            }
            if tolerance["fit_code"] not in texts:
                issues.append(
                    issue(
                        "DP-SOURCE-FIT-UNAPPROVED",
                        "source",
                        "fit code is not present as an exact approved input",
                        base + "/tolerance/fit_code",
                    )
                )
            return
        approved_values = [
            row["value"]["value_si"]
            for row in rows
            if row.get("value", {}).get("kind") == "quantity"
        ]
        for field in ("lower_si", "upper_si"):
            if not any(_close(value, tolerance[field]) for value in approved_values):
                issues.append(
                    issue(
                        "DP-SOURCE-TOLERANCE-UNAPPROVED",
                        "source",
                        f"{field} is not present in the approved inputs",
                        base + "/tolerance/" + field,
                    )
                )


class DimensionAttachmentValidator:
    def validate(self, plan, handoff) -> tuple[DimensionValidationIssue, ...]:
        issues: list[DimensionValidationIssue] = []
        views = by_id(handoff["views"], "view_id")
        features = by_id(handoff["manufacturing_features"], "feature_id")
        measurements = by_id(handoff["reference_measurements"], "measurement_id")
        for duplicate in _duplicates(handoff["views"], "view_id"):
            issues.append(
                issue(
                    "DP-ATTACHMENT-VIEW-AMBIGUOUS",
                    "attachment",
                    f"frozen view ID is duplicated: {duplicate}",
                    "/views",
                )
            )
        for duplicate in _duplicates(handoff["manufacturing_features"], "feature_id"):
            issues.append(
                issue(
                    "DP-ATTACHMENT-FEATURE-AMBIGUOUS",
                    "attachment",
                    f"frozen feature ID is duplicated: {duplicate}",
                    "/manufacturing_features",
                )
            )
        for index, dimension in enumerate(plan["dimensions"]):
            base = pointer("dimensions", index)
            view = views.get(dimension["target_view_id"])
            if view is None:
                issues.append(
                    issue(
                        "DP-ATTACHMENT-VIEW-MISSING",
                        "attachment",
                        "target view is absent from the frozen handoff",
                        base + "/target_view_id",
                    )
                )
                continue
            entities = by_id(view["projected_geometry"], "entity_id")
            for duplicate in _duplicates(view["projected_geometry"], "entity_id"):
                issues.append(
                    issue(
                        "DP-ATTACHMENT-ENTITY-AMBIGUOUS",
                        "attachment",
                        f"projected entity ID is duplicated: {duplicate}",
                        base + "/attachments",
                    )
                )
            attached_ids: set[str] = set()
            for attachment_index, attachment in enumerate(dimension["attachments"]):
                value_pointer = pointer(
                    "dimensions", index, "attachments", attachment_index
                )
                entity_id = attachment["entity_id"]
                entity = entities.get(entity_id)
                if entity is None:
                    issues.append(
                        issue(
                            "DP-ATTACHMENT-ENTITY-NOT-VISIBLE",
                            "attachment",
                            f"entity is not visible in target view: {entity_id}",
                            value_pointer + "/entity_id",
                        )
                    )
                    continue
                attached_ids.add(entity_id)
                if (
                    entity["model_persistent_reference"]
                    != attachment["model_persistent_reference"]
                    or entity["persistent_reference_kind"]
                    != attachment["persistent_reference_kind"]
                ):
                    issues.append(
                        issue(
                            "DP-ATTACHMENT-PERSISTENT-REFERENCE",
                            "attachment",
                            "attachment persistent reference differs from the frozen entity",
                            value_pointer + "/model_persistent_reference",
                        )
                    )
            for feature_id in dimension["feature_ids"]:
                if feature_id not in features:
                    issues.append(
                        issue(
                            "DP-ATTACHMENT-FEATURE-MISSING",
                            "attachment",
                            f"feature is absent from the frozen handoff: {feature_id}",
                            base + "/feature_ids",
                        )
                    )
            source = dimension["source"]
            if source["source_tier"] == "reference_geometry_measurement":
                for measurement_id in source["measurement_ids"]:
                    measurement = measurements.get(measurement_id)
                    if measurement is None:
                        continue
                    if measurement["view_id"] != dimension["target_view_id"] or not set(
                        measurement["entity_ids"]
                    ).issubset(attached_ids):
                        issues.append(
                            issue(
                                "DP-ATTACHMENT-REFERENCE-MISMATCH",
                                "attachment",
                                "reference measurement view/entities are not fully attached",
                                base + "/attachments",
                            )
                        )
        return stable_issues(issues)


class DimensionSemanticsValidator:
    def validate(self, plan, handoff) -> tuple[DimensionValidationIssue, ...]:
        issues: list[DimensionValidationIssue] = []
        try:
            dimension_plan_from_mapping(plan)
        except ValidationError as exc:
            for row in exc.errors(include_url=False):
                issues.append(
                    issue(
                        "DP-SEMANTICS-DOMAIN",
                        "semantics",
                        row["msg"],
                        pointer(*row["loc"]),
                    )
                )
            return stable_issues(issues)

        for index, dimension in enumerate(plan["dimensions"]):
            base = pointer("dimensions", index)
            entity_ids = {row["entity_id"] for row in dimension["attachments"]}
            roles = {row["role"] for row in dimension["attachments"]}
            if dimension["kind"] in _TWO_ATTACHMENT_KINDS and len(entity_ids) < 2:
                issues.append(
                    issue(
                        "DP-SEMANTICS-ATTACHMENT-ARITY",
                        "semantics",
                        f"{dimension['kind']} requires two distinct attached entities",
                        base + "/attachments",
                    )
                )
            if dimension["kind"] in _TWO_ATTACHMENT_KINDS and not {
                "first",
                "second",
            }.issubset(roles):
                issues.append(
                    issue(
                        "DP-SEMANTICS-ATTACHMENT-ROLES",
                        "semantics",
                        f"{dimension['kind']} requires first and second attachment roles",
                        base + "/attachments",
                    )
                )
            if dimension["kind"] == "symmetric" and "symmetry_axis" not in roles:
                issues.append(
                    issue(
                        "DP-SEMANTICS-SYMMETRY-AXIS",
                        "semantics",
                        "symmetric dimensions require a symmetry_axis attachment",
                        base + "/attachments",
                    )
                )
            hierarchy = dimension["hierarchy"]
            if hierarchy["chain_id"] is not None and hierarchy["baseline_id"] is not None:
                issues.append(
                    issue(
                        "DP-SEMANTICS-HIERARCHY-MODE",
                        "semantics",
                        "a dimension cannot belong to a chain and baseline simultaneously",
                        base + "/hierarchy",
                    )
                )
            display = dimension["display_format"]
            quantity = dimension["value"]["quantity_kind"]
            allowed_units = {
                "length": {"document_default", "mm", "inch"},
                "angle": {"document_default", "degree"},
                "count": {"document_default", "count"},
            }[quantity]
            if display["unit"] not in allowed_units:
                issues.append(
                    issue(
                        "DP-SEMANTICS-DISPLAY-UNIT",
                        "semantics",
                        "display unit is incompatible with the quantity kind",
                        base + "/display_format/unit",
                    )
                )
            if dimension["kind"] == "reference" and not display["show_parentheses"]:
                issues.append(
                    issue(
                        "DP-SEMANTICS-REFERENCE-DISPLAY",
                        "semantics",
                        "reference dimensions must display parentheses",
                        base + "/display_format/show_parentheses",
                    )
                )
        return stable_issues(issues)


class DimensionCoverageValidator:
    def validate(self, plan, handoff) -> tuple[DimensionValidationIssue, ...]:
        issues: list[DimensionValidationIssue] = []
        covered_features = {
            feature_id
            for dimension in plan["dimensions"]
            if dimension["hierarchy"]["level"] != "reference"
            for feature_id in dimension["feature_ids"]
        }
        for feature in handoff["manufacturing_features"]:
            if feature["feature_id"] not in covered_features:
                issues.append(
                    issue(
                        "DP-COVERAGE-FEATURE-MISSING",
                        "coverage",
                        "manufacturing feature has no non-reference dimension: "
                        + feature["feature_id"],
                        "/dimensions",
                    )
                )
        consumed_inputs = {
            source_id
            for dimension in plan["dimensions"]
            for source_id in dimension["source"].get("approved_input_ids", [])
        }
        for approved in handoff["approved_user_inputs"]:
            if approved["input_id"] not in consumed_inputs:
                issues.append(
                    issue(
                        "DP-COVERAGE-APPROVED-INPUT-MISSING",
                        "coverage",
                        f"approved user input is not consumed: {approved['input_id']}",
                        "/dimensions",
                    )
                )
        return stable_issues(issues)


class DimensionRedundancyValidator:
    def validate(self, plan, handoff) -> tuple[DimensionValidationIssue, ...]:
        del handoff
        issues: list[DimensionValidationIssue] = []
        signatures: dict[tuple, tuple[int, Mapping[str, Any]]] = {}
        chain_edges: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        for index, dimension in enumerate(plan["dimensions"]):
            entities = tuple(sorted({row["entity_id"] for row in dimension["attachments"]}))
            signature = (
                dimension["kind"],
                dimension["target_view_id"],
                entities,
                tuple(sorted(dimension["feature_ids"])),
            )
            previous = signatures.get(signature)
            if previous is not None:
                previous_index, previous_dimension = previous
                code = (
                    "DP-REDUNDANCY-DUPLICATE"
                    if _close(
                        previous_dimension["value"]["nominal_si"],
                        dimension["value"]["nominal_si"],
                    )
                    and previous_dimension["tolerance"] == dimension["tolerance"]
                    else "DP-REDUNDANCY-CONFLICT"
                )
                issues.append(
                    issue(
                        code,
                        "redundancy",
                        f"dimension duplicates/conflicts with dimensions[{previous_index}]",
                        pointer("dimensions", index),
                    )
                )
            else:
                signatures[signature] = (index, dimension)
            chain_id = dimension["hierarchy"]["chain_id"]
            if chain_id is not None and len(entities) == 2:
                chain_edges[chain_id].append((index, entities[0], entities[1]))

        for chain_id, edges in chain_edges.items():
            parents: dict[str, str] = {}

            def root(value: str) -> str:
                parents.setdefault(value, value)
                while parents[value] != value:
                    parents[value] = parents[parents[value]]
                    value = parents[value]
                return value

            for index, left, right in edges:
                left_root, right_root = root(left), root(right)
                if left_root == right_root:
                    issues.append(
                        issue(
                            "DP-REDUNDANCY-CLOSED-CHAIN",
                            "redundancy",
                            f"dimension chain closes a forbidden loop: {chain_id}",
                            pointer("dimensions", index, "hierarchy", "chain_id"),
                        )
                    )
                else:
                    parents[left_root] = right_root
        return stable_issues(issues)


class DimensionLayoutValidator:
    def validate(self, plan, handoff) -> tuple[DimensionValidationIssue, ...]:
        issues: list[DimensionValidationIssue] = []
        views = by_id(handoff["views"], "view_id")
        zones = by_id(handoff["dimension_zones"], "id")
        sheet = rect(handoff["drawing_context"]["sheet_bounds_m"])
        positioned: list[tuple[int, tuple[float, float], float]] = []
        for duplicate in _duplicates(handoff["dimension_zones"], "id"):
            issues.append(
                issue(
                    "DP-LAYOUT-ZONE-AMBIGUOUS",
                    "layout",
                    f"dimension zone ID is duplicated: {duplicate}",
                    "/dimension_zones",
                )
            )
        for index, dimension in enumerate(plan["dimensions"]):
            base = pointer("dimensions", index)
            position = point(dimension["initial_position_sheet_m"])
            zone = zones.get(dimension["dimension_zone_id"])
            if zone is None:
                issues.append(
                    issue(
                        "DP-LAYOUT-ZONE-MISSING",
                        "layout",
                        "dimension zone is absent from the frozen handoff",
                        base + "/dimension_zone_id",
                    )
                )
                continue
            zone_bounds = rect(zone.get("bounds_sheet_m"))
            if zone.get("view_id") != dimension["target_view_id"]:
                issues.append(
                    issue(
                        "DP-LAYOUT-ZONE-VIEW",
                        "layout",
                        "dimension zone belongs to a different target view",
                        base + "/dimension_zone_id",
                    )
                )
            if sheet is None or zone_bounds is None or position is None:
                issues.append(
                    issue(
                        "DP-LAYOUT-NONFINITE",
                        "layout",
                        "sheet, zone and initial position must be finite valid geometry",
                        base + "/initial_position_sheet_m",
                    )
                )
                continue
            if not rect_contains(sheet, zone_bounds):
                issues.append(
                    issue(
                        "DP-LAYOUT-ZONE-SHEET",
                        "layout",
                        "dimension zone must lie completely inside the sheet",
                        base + "/dimension_zone_id",
                    )
                )
            margin = float(dimension["verification_tolerance"]["position_abs_m"])
            inset = (
                zone_bounds[0] + margin,
                zone_bounds[1] + margin,
                zone_bounds[2] - margin,
                zone_bounds[3] - margin,
            )
            if (
                not contains(sheet, position)
                or inset[0] > inset[2]
                or inset[1] > inset[3]
                or not contains(inset, position)
            ):
                issues.append(
                    issue(
                        "DP-LAYOUT-UNSTABLE-POSITION",
                        "layout",
                        "initial position must lie inside its zone with verification margin",
                        base + "/initial_position_sheet_m",
                    )
                )
            view = views.get(dimension["target_view_id"])
            view_bounds = rect(view.get("bounds_sheet_m")) if view else None
            if view_bounds is not None and overlaps(zone_bounds, view_bounds):
                issues.append(
                    issue(
                        "DP-LAYOUT-ZONE-VIEW-OVERLAP",
                        "layout",
                        "dimension zone overlaps the target view bounds",
                        base + "/dimension_zone_id",
                    )
                )
            if view_bounds is not None and contains(view_bounds, position):
                issues.append(
                    issue(
                        "DP-LAYOUT-POSITION-IN-VIEW",
                        "layout",
                        "dimension text position must not lie inside the target view bounds",
                        base + "/initial_position_sheet_m",
                    )
                )
            if view is not None:
                for annotation in view["existing_annotations"]:
                    envelope = rect(annotation["display_envelope_sheet_m"])
                    if envelope is not None and contains(envelope, position):
                        issues.append(
                            issue(
                                "DP-LAYOUT-EXISTING-ANNOTATION-COLLISION",
                                "layout",
                                "initial position collides with an existing annotation envelope",
                                base + "/initial_position_sheet_m",
                            )
                        )
            positioned.append((index, position, margin))

        for left in range(len(positioned)):
            left_index, left_point, left_margin = positioned[left]
            for right_index, right_point, right_margin in positioned[left + 1 :]:
                if math.dist(left_point, right_point) <= max(left_margin, right_margin):
                    issues.append(
                        issue(
                            "DP-LAYOUT-DIMENSION-COLLISION",
                            "layout",
                            f"initial position collides with dimensions[{left_index}]",
                            pointer("dimensions", right_index, "initial_position_sheet_m"),
                        )
                    )
        return stable_issues(issues)


def _close(left: object, right: object) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and not isinstance(left, bool)
        and not isinstance(right, bool)
        and math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=_VALUE_TOLERANCE,
        )
    )


def _duplicates(rows, key: str) -> tuple[str, ...]:
    counts = Counter(
        row.get(key)
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get(key), str)
    )
    return tuple(sorted(value for value, count in counts.items() if count > 1))

