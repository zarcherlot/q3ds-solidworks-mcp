"""Evidence-bound first-draft DimensionPlan candidate construction.

This is a planning aid, not a production publication boundary.  Recipes identify exact frozen
handoff records; the builder supplies values, persistent references, artifact hashes and producer
identity from repository-owned evidence, then runs the full deterministic validator.  It never
contacts SolidWorks and never turns capability_blocked into supported.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .handoff import file_sha256, validate_dimension_planning_handoff
from .planning_models import (
    DimensionPlanningRequest,
    canonical_json_sha256,
    dimension_plan_from_mapping,
)
from .validators import RepositoryDimensionPlanValidator


PACKAGE_ROOT = Path(__file__).resolve().parent
PROMPT_MANIFEST_PATH = PACKAGE_ROOT / "prompt_packs" / "native-v1" / "manifest.json"
SCHEMA_URI = "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json"
RECIPE_PROTOCOL = "solidworks-dimension-first-draft-recipe"


class DimensionFirstDraftError(ValueError):
    """Raised when a recipe is ambiguous or not exactly bound to its handoff."""


def build_first_draft_candidate(
    handoff_path: Path, recipe_candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], DimensionPlanningRequest, dict[str, Any]]:
    handoff_file = handoff_path.resolve(strict=True)
    handoff = validate_dimension_planning_handoff(_load_json(handoff_file))
    recipe = _strict_json_object(recipe_candidate, "first-draft recipe")
    _validate_recipe_envelope(recipe)

    manifest = _load_json(PROMPT_MANIFEST_PATH)
    producer = manifest.get("producer")
    if not isinstance(producer, dict):
        raise DimensionFirstDraftError("native-v1 prompt manifest has no producer")
    upstream = _upstream_by_role(handoff)
    dimensions = [
        _build_dimension(spec, handoff, index, recipe["schema_version"])
        for index, spec in enumerate(recipe["dimensions"])
    ]
    plan = {
        "$schema": SCHEMA_URI,
        "protocol_id": "solidworks-dimension-plan",
        "schema_version": "1.0",
        "plan_id": recipe["plan_id"],
        "created_at_utc": recipe["created_at_utc"],
        "producer": producer,
        "execution_policy": {
            "on_integrity_mismatch": "fail",
            "on_selection_ambiguity": "fail",
            "on_unsupported_dimension": "fail",
            "on_layout_violation": "fail",
            "allow_source_model_write": False,
            "allow_upstream_drawing_overwrite": False,
            "allow_partial_commit": False,
        },
        "handoff": {"path": str(handoff_file), "sha256": file_sha256(handoff_file)},
        "handoff_id": handoff["handoff_id"],
        "source_model": {
            "path": handoff["source_model"]["path"],
            "sha256": handoff["source_model"]["sha256"],
        },
        "source_drawing": _artifact_from_upstream(upstream, "verified_drawing"),
        "view_plan": _artifact_from_upstream(upstream, "view_plan"),
        "verification_sidecar": _artifact_from_upstream(
            upstream, "verification_sidecar"
        ),
        "configuration": handoff["source_model"]["configuration"],
        "dimensions": dimensions,
        "assumptions": list(recipe["assumptions"]),
        "open_questions": [],
    }
    normalized = dimension_plan_from_mapping(plan).execution_dict()
    request = DimensionPlanningRequest(
        handoff_path=str(handoff_file),
        handoff_sha256=file_sha256(handoff_file),
        planner_profile="production",
        publication_directory=str(handoff_file.parent),
        user_requirements={"source_drawing_read_only": True},
    )
    validation = RepositoryDimensionPlanValidator().validate(normalized, request)
    if not validation.engineering_passed:
        details = "; ".join(
            f"{issue.code} {issue.json_pointer or ''}" for issue in validation.issues
        )
        raise DimensionFirstDraftError(
            "first-draft candidate failed deterministic engineering gates: " + details
        )
    report = {
        "protocol_id": "solidworks-dimension-first-draft-validation",
        "schema_version": "1.0",
        "plan_id": normalized["plan_id"],
        "handoff_id": handoff["handoff_id"],
        "handoff_sha256": file_sha256(handoff_file),
        "plan_canonical_sha256": canonical_json_sha256(normalized, "DimensionPlan"),
        "engineering_passed": True,
        "execution_readiness": (
            "supported" if validation.capability == "pass" else "capability_blocked"
        ),
        "validation": validation.model_dump(mode="json"),
        "publication_required": True,
    }
    return normalized, request, report


def _build_dimension(
    spec_candidate: object,
    handoff: Mapping[str, Any],
    index: int,
    recipe_version: str,
) -> dict[str, Any]:
    spec = _strict_json_object(spec_candidate, f"dimensions[{index}]")
    if recipe_version == "1.1":
        return _build_dimension_v1_1(spec, handoff, index)
    required = {
        "dimension_id",
        "kind",
        "source_dimension_id",
        "target_view_id",
        "attachments",
        "feature_ids",
        "dimension_zone_id",
        "initial_position_sheet_m",
        "display_format",
        "hierarchy",
        "verification_tolerance",
    }
    if set(spec) != required:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] properties must be exactly {sorted(required)}"
        )
    model_dimensions = _by_id(
        handoff["model_driven_dimensions"], "dimension_id", "model dimension"
    )
    source_id = spec["source_dimension_id"]
    source = model_dimensions.get(source_id)
    if source is None:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] source dimension is absent: {source_id}"
        )
    nominal = source.get("value_si")
    if not isinstance(nominal, (int, float)) or isinstance(nominal, bool) or nominal <= 0:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] source dimension has no positive finite value"
        )

    views = _by_id(handoff["views"], "view_id", "view")
    view = views.get(spec["target_view_id"])
    if view is None:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] target view is absent: {spec['target_view_id']}"
        )
    entities = _by_id(view["projected_geometry"], "entity_id", "projected entity")
    attachments: list[dict[str, Any]] = []
    if not isinstance(spec["attachments"], list) or not spec["attachments"]:
        raise DimensionFirstDraftError(f"dimensions[{index}] attachments must be non-empty")
    for attachment_index, raw in enumerate(spec["attachments"]):
        attachment = _strict_json_object(
            raw, f"dimensions[{index}].attachments[{attachment_index}]"
        )
        if set(attachment) != {"attachment_id", "entity_id", "role"}:
            raise DimensionFirstDraftError(
                f"dimensions[{index}].attachments[{attachment_index}] has unexpected fields"
            )
        entity = entities.get(attachment["entity_id"])
        if entity is None:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] attachment entity is not visible in the target view: "
                f"{attachment['entity_id']}"
            )
        attachments.append(
            {
                "attachment_id": attachment["attachment_id"],
                "entity_id": attachment["entity_id"],
                "model_persistent_reference": entity["model_persistent_reference"],
                "persistent_reference_kind": entity["persistent_reference_kind"],
                "role": attachment["role"],
            }
        )

    features = _by_id(
        handoff["manufacturing_features"], "feature_id", "manufacturing feature"
    )
    feature_ids = spec["feature_ids"]
    if not isinstance(feature_ids, list) or not feature_ids:
        raise DimensionFirstDraftError(f"dimensions[{index}] feature_ids must be non-empty")
    missing_features = [feature_id for feature_id in feature_ids if feature_id not in features]
    if missing_features:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] manufacturing features are absent: {missing_features}"
        )
    zones = _by_id(handoff["dimension_zones"], "id", "dimension zone")
    zone = zones.get(spec["dimension_zone_id"])
    if zone is None or zone.get("view_id") != spec["target_view_id"]:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] dimension zone is absent or belongs to another view"
        )

    kind = spec["kind"]
    quantity_kind = "angle" if kind == "angular" else (
        "count" if kind == "hole_quantity" else "length"
    )
    return {
        "dimension_id": spec["dimension_id"],
        "kind": kind,
        "source": {
            "source_tier": "model_or_pmi",
            "handoff_collection": "model_driven_dimensions",
            "source_ids": [source_id],
        },
        "target_view_id": spec["target_view_id"],
        "attachments": attachments,
        "feature_ids": feature_ids,
        "value": {
            "value_mode": "model_driven",
            "quantity_kind": quantity_kind,
            "nominal_si": float(nominal),
        },
        "tolerance": None,
        "display_format": spec["display_format"],
        "dimension_zone_id": spec["dimension_zone_id"],
        "hierarchy": spec["hierarchy"],
        "initial_position_sheet_m": spec["initial_position_sheet_m"],
        "verification_tolerance": spec["verification_tolerance"],
    }


def _build_dimension_v1_1(
    spec: Mapping[str, Any], handoff: Mapping[str, Any], index: int
) -> dict[str, Any]:
    """Build one advanced F7 candidate only from explicitly frozen evidence.

    Version 1.1 adds the three trusted source tiers and tolerance payload required by the
    complete F7 matrix. Values remain candidate input, but the repository validator below must
    find the exact nominal/tolerance in the bound handoff before this function can return a plan.
    """

    required = {
        "dimension_id",
        "kind",
        "source",
        "value",
        "tolerance",
        "target_view_id",
        "attachments",
        "feature_ids",
        "dimension_zone_id",
        "initial_position_sheet_m",
        "display_format",
        "hierarchy",
        "verification_tolerance",
    }
    if set(spec) != required:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] properties must be exactly {sorted(required)}"
        )

    views = _by_id(handoff["views"], "view_id", "view")
    view = views.get(spec["target_view_id"])
    if view is None:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] target view is absent: {spec['target_view_id']}"
        )
    entities = _by_id(view["projected_geometry"], "entity_id", "projected entity")
    raw_attachments = spec["attachments"]
    if not isinstance(raw_attachments, list) or not raw_attachments:
        raise DimensionFirstDraftError(f"dimensions[{index}] attachments must be non-empty")
    attachments: list[dict[str, Any]] = []
    for attachment_index, raw in enumerate(raw_attachments):
        attachment = _strict_json_object(
            raw, f"dimensions[{index}].attachments[{attachment_index}]"
        )
        if set(attachment) != {"attachment_id", "entity_id", "role"}:
            raise DimensionFirstDraftError(
                f"dimensions[{index}].attachments[{attachment_index}] has unexpected fields"
            )
        entity = entities.get(attachment["entity_id"])
        if entity is None:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] attachment entity is not visible in the target view: "
                f"{attachment['entity_id']}"
            )
        attachments.append(
            {
                "attachment_id": attachment["attachment_id"],
                "entity_id": attachment["entity_id"],
                "model_persistent_reference": entity["model_persistent_reference"],
                "persistent_reference_kind": entity["persistent_reference_kind"],
                "role": attachment["role"],
            }
        )

    features = _by_id(
        handoff["manufacturing_features"], "feature_id", "manufacturing feature"
    )
    feature_ids = spec["feature_ids"]
    if not isinstance(feature_ids, list) or not feature_ids:
        raise DimensionFirstDraftError(f"dimensions[{index}] feature_ids must be non-empty")
    missing_features = [feature_id for feature_id in feature_ids if feature_id not in features]
    if missing_features:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] manufacturing features are absent: {missing_features}"
        )
    zones = _by_id(handoff["dimension_zones"], "id", "dimension zone")
    zone = zones.get(spec["dimension_zone_id"])
    if zone is None or zone.get("view_id") != spec["target_view_id"]:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] dimension zone is absent or belongs to another view"
        )

    source = _strict_json_object(spec["source"], f"dimensions[{index}].source")
    tier = source.get("source_tier")
    if tier == "model_or_pmi":
        if set(source) != {"source_tier", "handoff_collection", "source_ids"}:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] model/PMI source is not strict"
            )
        collection_name = source.get("handoff_collection")
        collection_keys = {
            "model_driven_dimensions": "dimension_id",
            "pmi_annotations": "annotation_id",
            "manufacturing_features": "feature_id",
        }
        if collection_name not in collection_keys:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] model/PMI collection is unknown"
            )
        collection = _by_id(
            handoff[collection_name], collection_keys[collection_name], collection_name
        )
        source_ids = _nonempty_unique_string_list(
            source["source_ids"], f"dimensions[{index}].source.source_ids"
        )
        missing = [source_id for source_id in source_ids if source_id not in collection]
    elif tier == "user_confirmed_input":
        if set(source) != {"source_tier", "approved_input_ids"}:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] approved source is not strict"
            )
        collection = _by_id(handoff["approved_user_inputs"], "input_id", "approved input")
        source_ids = _nonempty_unique_string_list(
            source["approved_input_ids"],
            f"dimensions[{index}].source.approved_input_ids",
        )
        missing = [source_id for source_id in source_ids if source_id not in collection]
    elif tier == "reference_geometry_measurement":
        if set(source) != {
            "source_tier",
            "measurement_ids",
            "manufacturing_requirement",
        } or source.get("manufacturing_requirement") is not False:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] reference source is not strict/non-manufacturing"
            )
        collection = _by_id(
            handoff["reference_measurements"], "measurement_id", "reference measurement"
        )
        source_ids = _nonempty_unique_string_list(
            source["measurement_ids"],
            f"dimensions[{index}].source.measurement_ids",
        )
        missing = [source_id for source_id in source_ids if source_id not in collection]
    else:
        raise DimensionFirstDraftError(f"dimensions[{index}] source tier is unknown: {tier}")
    if missing:
        raise DimensionFirstDraftError(
            f"dimensions[{index}] trusted source IDs are absent: {missing}"
        )

    value = _strict_json_object(spec["value"], f"dimensions[{index}].value")
    if set(value) != {"value_mode", "quantity_kind", "nominal_si"}:
        raise DimensionFirstDraftError(f"dimensions[{index}] value is not strict")
    tolerance = spec["tolerance"]
    if tolerance is not None:
        tolerance = _strict_json_object(tolerance, f"dimensions[{index}].tolerance")
        if set(tolerance) != {"kind", "lower_si", "upper_si", "fit_code"}:
            raise DimensionFirstDraftError(
                f"dimensions[{index}] tolerance is not strict"
            )

    return {
        "dimension_id": spec["dimension_id"],
        "kind": spec["kind"],
        "source": source,
        "target_view_id": spec["target_view_id"],
        "attachments": attachments,
        "feature_ids": list(feature_ids),
        "value": value,
        "tolerance": tolerance,
        "display_format": _strict_json_object(
            spec["display_format"], f"dimensions[{index}].display_format"
        ),
        "dimension_zone_id": spec["dimension_zone_id"],
        "hierarchy": _strict_json_object(
            spec["hierarchy"], f"dimensions[{index}].hierarchy"
        ),
        "initial_position_sheet_m": list(spec["initial_position_sheet_m"]),
        "verification_tolerance": _strict_json_object(
            spec["verification_tolerance"],
            f"dimensions[{index}].verification_tolerance",
        ),
    }


def _validate_recipe_envelope(recipe: Mapping[str, Any]) -> None:
    required = {
        "protocol_id",
        "schema_version",
        "plan_id",
        "created_at_utc",
        "dimensions",
        "assumptions",
    }
    if set(recipe) != required:
        raise DimensionFirstDraftError(
            "first-draft recipe properties must be exactly " + str(sorted(required))
        )
    if recipe.get("protocol_id") != RECIPE_PROTOCOL or recipe.get("schema_version") not in {
        "1.0",
        "1.1",
    }:
        raise DimensionFirstDraftError("unexpected first-draft recipe protocol/version")
    if not isinstance(recipe.get("dimensions"), list) or not recipe["dimensions"]:
        raise DimensionFirstDraftError("first-draft recipe requires dimensions")
    if not isinstance(recipe.get("assumptions"), list):
        raise DimensionFirstDraftError("first-draft assumptions must be an array")


def _artifact_from_upstream(
    upstream: Mapping[str, Mapping[str, Any]], role: str
) -> dict[str, str]:
    row = upstream.get(role)
    if row is None or row.get("sha256_before") != row.get("sha256_after"):
        raise DimensionFirstDraftError(f"upstream artifact is missing or changed: {role}")
    return {"path": row["path"], "sha256": row["sha256_after"]}


def _upstream_by_role(handoff: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return _by_id(handoff["upstream_artifacts"], "role", "upstream artifact")


def _by_id(rows: object, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise DimensionFirstDraftError(f"handoff {label} collection is invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get(key), str):
            raise DimensionFirstDraftError(f"handoff {label} has no {key}")
        value = row[key]
        if value in result:
            raise DimensionFirstDraftError(f"handoff {label} ID is duplicated: {value}")
        result[value] = row
    return result


def _strict_json_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DimensionFirstDraftError(f"{label} must be an object")
    try:
        result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DimensionFirstDraftError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise DimensionFirstDraftError(f"{label} must be an object")
    return result


def _nonempty_unique_string_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise DimensionFirstDraftError(f"{label} must be a non-empty string array")
    if len(set(value)) != len(value):
        raise DimensionFirstDraftError(f"{label} must not contain duplicate IDs")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DimensionFirstDraftError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionFirstDraftError(f"JSON artifact must contain an object: {path}")
    return value
