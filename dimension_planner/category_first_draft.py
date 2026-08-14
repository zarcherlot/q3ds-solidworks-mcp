"""Build a provisional six-category DimensionPlan candidate set.

The category profile is deliberately an evidence-binding file, not an engineering ruleset.  It
binds each provisional category to exact model-driven dimensions from one complete immutable
handoff.  The builder derives visible attachments, zones, feature IDs and display defaults from
that handoff, then delegates every candidate to :mod:`dimension_planner.first_draft` so the full
repository deterministic gate chain still applies.

Using one handoff as a proxy for more than one category is permitted only for draft output.  The
summary records that fact and must never be accepted as F7 live evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .first_draft import (
    DimensionFirstDraftError,
    build_first_draft_candidate,
)
from .handoff import file_sha256, validate_dimension_planning_handoff


PROFILE_PROTOCOL = "solidworks-dimension-six-category-first-draft-profile"
CATEGORIES = (
    "plate",
    "shaft_sleeve",
    "bracket",
    "flange",
    "slot_cavity",
    "threaded",
)
DIMENSION_KINDS = (
    "linear",
    "aligned",
    "diameter",
    "radius",
    "angular",
    "reference",
    "hole_diameter",
    "hole_depth",
    "hole_quantity",
    "hole_spacing",
    "hole_group_location",
    "overall",
    "step",
    "boss",
    "slot",
    "chamfer",
    "fillet",
    "symmetric",
)
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


def build_six_category_first_drafts(
    handoff_path: Path, profile_candidate: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return six engineering-valid candidates plus an explicitly provisional summary."""

    handoff_file = handoff_path.resolve(strict=True)
    handoff = validate_dimension_planning_handoff(_load_json(handoff_file))
    profile = _strict_object(profile_candidate, "six-category profile")
    _validate_profile(profile)

    views = _by_id(handoff["views"], "view_id", "view")
    zones = _by_id(handoff["dimension_zones"], "id", "dimension zone")
    features = [row["feature_id"] for row in handoff["manufacturing_features"]]
    if not features:
        raise DimensionFirstDraftError(
            "six-category draft requires at least one manufacturing feature"
        )

    preferred_views = profile["preferred_views"]
    resolved_views: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for view_id in preferred_views:
        view = views.get(view_id)
        if view is None:
            raise DimensionFirstDraftError(f"preferred view is absent: {view_id}")
        zone = next(
            (row for row in zones.values() if row.get("view_id") == view_id), None
        )
        if zone is None:
            raise DimensionFirstDraftError(
                f"preferred view has no frozen dimension zone: {view_id}"
            )
        resolved_views.append((view_id, view, zone))

    candidates: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    for category_index, category_row in enumerate(profile["categories"]):
        category = category_row["category"]
        specs = []
        for dimension_index, binding in enumerate(category_row["dimensions"]):
            view_id, view, zone = resolved_views[dimension_index % len(resolved_views)]
            entities = view["projected_geometry"]
            required_count = 3 if binding["kind"] == "symmetric" else (
                2 if binding["kind"] in _TWO_ATTACHMENT_KINDS else 1
            )
            if len(entities) < required_count:
                raise DimensionFirstDraftError(
                    f"{category}/{binding['kind']} requires {required_count} visible "
                    f"entities in {view_id}"
                )
            roles = (
                ["first", "second", "symmetry_axis"]
                if binding["kind"] == "symmetric"
                else ["first", "second"]
                if required_count == 2
                else ["arc"]
            )
            attachments = [
                {
                    "attachment_id": (
                        f"A-{category_index}-{dimension_index}-{attachment_index}"
                    ),
                    "entity_id": entities[attachment_index]["entity_id"],
                    "role": role,
                }
                for attachment_index, role in enumerate(roles)
            ]
            specs.append(
                {
                    "dimension_id": f"D-{category}-{binding['kind']}",
                    "kind": binding["kind"],
                    "source_dimension_id": binding["source_dimension_id"],
                    "target_view_id": view_id,
                    "attachments": attachments,
                    "feature_ids": features,
                    "dimension_zone_id": zone["id"],
                    "initial_position_sheet_m": _zone_center(zone),
                    "display_format": _display_format(binding["kind"]),
                    "hierarchy": _hierarchy(binding["kind"], dimension_index),
                    "verification_tolerance": {
                        "value_abs_si": 1e-9,
                        "position_abs_m": 1e-6,
                        "attachment_count_exact": True,
                        "display_text_exact": False,
                    },
                }
            )
        recipe = {
            "protocol_id": "solidworks-dimension-first-draft-recipe",
            "schema_version": "1.0",
            "plan_id": category_row["plan_id"],
            "created_at_utc": profile["created_at_utc"],
            "dimensions": specs,
            "assumptions": [
                *profile["assumptions"],
                f"Provisional category proxy: {category}.",
            ],
        }
        plan, request, validation = build_first_draft_candidate(handoff_file, recipe)
        candidates[category] = {
            "plan": plan,
            "request": request.model_dump(mode="json"),
            "validation": validation,
        }
        cases.append(
            {
                "category": category,
                "plan_id": plan["plan_id"],
                "dimension_kinds": [row["kind"] for row in plan["dimensions"]],
                "dimension_count": len(plan["dimensions"]),
                "engineering_passed": validation["engineering_passed"],
                "execution_readiness": validation["execution_readiness"],
                "plan_canonical_sha256": validation["plan_canonical_sha256"],
            }
        )

    summary = {
        "protocol_id": "solidworks-dimension-six-category-first-draft-summary",
        "schema_version": "1.0",
        "profile_id": profile["profile_id"],
        "handoff_path": str(handoff_file),
        "handoff_sha256": file_sha256(handoff_file),
        "category_evidence": "proxy",
        "eligible_for_f7_promotion": False,
        "publication_required": True,
        "category_count": len(cases),
        "dimension_kind_count": len(
            {kind for case in cases for kind in case["dimension_kinds"]}
        ),
        "cases": cases,
    }
    return candidates, summary


def _validate_profile(profile: Mapping[str, Any]) -> None:
    expected = {
        "protocol_id",
        "schema_version",
        "profile_id",
        "created_at_utc",
        "preferred_views",
        "categories",
        "assumptions",
    }
    if set(profile) != expected:
        raise DimensionFirstDraftError(
            "six-category profile properties must be exactly " + str(sorted(expected))
        )
    if (
        profile["protocol_id"] != PROFILE_PROTOCOL
        or profile["schema_version"] != "1.0"
    ):
        raise DimensionFirstDraftError("unexpected six-category profile protocol/version")
    if not isinstance(profile["preferred_views"], list) or len(
        profile["preferred_views"]
    ) < 3:
        raise DimensionFirstDraftError("profile requires at least three preferred views")
    if not isinstance(profile["assumptions"], list):
        raise DimensionFirstDraftError("profile assumptions must be an array")
    rows = profile["categories"]
    if not isinstance(rows, list) or len(rows) != len(CATEGORIES):
        raise DimensionFirstDraftError("profile must contain exactly six category rows")
    observed_categories: list[str] = []
    observed_kinds: list[str] = []
    for index, raw in enumerate(rows):
        row = _strict_object(raw, f"categories[{index}]")
        if set(row) != {"category", "plan_id", "dimensions"}:
            raise DimensionFirstDraftError(f"categories[{index}] has unexpected fields")
        observed_categories.append(row["category"])
        dimensions = row["dimensions"]
        if not isinstance(dimensions, list) or len(dimensions) != 3:
            raise DimensionFirstDraftError(
                f"categories[{index}] must bind exactly three dimensions"
            )
        for dimension_index, raw_binding in enumerate(dimensions):
            binding = _strict_object(
                raw_binding, f"categories[{index}].dimensions[{dimension_index}]"
            )
            if set(binding) != {"kind", "source_dimension_id"}:
                raise DimensionFirstDraftError("category dimension binding is not strict")
            observed_kinds.append(binding["kind"])
    if tuple(observed_categories) != CATEGORIES:
        raise DimensionFirstDraftError(
            "category rows must use the canonical six-category order"
        )
    if tuple(observed_kinds) != DIMENSION_KINDS:
        raise DimensionFirstDraftError(
            "profile must cover the canonical 18 DimensionPlan kinds exactly once"
        )


def _display_format(kind: str) -> dict[str, Any]:
    quantity = "angle" if kind == "angular" else (
        "count" if kind == "hole_quantity" else "length"
    )
    return {
        "unit": {"angle": "degree", "count": "count", "length": "mm"}[quantity],
        "precision": 1 if quantity == "angle" else 0 if quantity == "count" else 3,
        "prefix": "",
        "suffix": "",
        "show_parentheses": kind == "reference",
        "show_units": False,
        "dual_units": False,
    }


def _hierarchy(kind: str, priority: int) -> dict[str, Any]:
    return {
        "level": "reference" if kind == "reference" else "manufacturing",
        "priority": priority,
        "chain_id": None,
        "baseline_id": None,
    }


def _zone_center(zone: Mapping[str, Any]) -> list[float]:
    bounds = zone["bounds_sheet_m"]
    if isinstance(bounds, Mapping):
        x_min, y_min, x_max, y_max = (
            bounds["x_min_m"],
            bounds["y_min_m"],
            bounds["x_max_m"],
            bounds["y_max_m"],
        )
    else:
        x_min, y_min, x_max, y_max = bounds
    return [
        (float(x_min) + float(x_max)) / 2,
        (float(y_min) + float(y_max)) / 2,
    ]


def _by_id(rows: object, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise DimensionFirstDraftError(f"handoff {label} collection is invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get(key), str):
            raise DimensionFirstDraftError(f"handoff {label} has no {key}")
        if row[key] in result:
            raise DimensionFirstDraftError(
                f"handoff {label} ID is duplicated: {row[key]}"
            )
        result[row[key]] = row
    return result


def _strict_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DimensionFirstDraftError(f"{label} must be an object")
    try:
        result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DimensionFirstDraftError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise DimensionFirstDraftError(f"{label} must be an object")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DimensionFirstDraftError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionFirstDraftError(f"JSON artifact must contain an object: {path}")
    return value
