"""G1 immutable layout-handoff construction and publication validation.

SolidWorks boundary readback remains in the C# execution service.  This module
binds independently verified F-stage artifacts to the live-complete G0
capability evidence and rejects any published handoff that weakens those
bindings or hides an inexact collision boundary.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from drawing_planner.planning_models import canonical_json_sha256


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_ROOT = PACKAGE_ROOT / "contracts"
CAPABILITY_MANIFEST_PATH = PACKAGE_ROOT / "capabilities" / "current.json"
REQUEST_CONTRACT_PATH = CONTRACT_ROOT / "drawing-layout-handoff-request.schema.json"
HANDOFF_CONTRACT_PATH = CONTRACT_ROOT / "drawing-layout-handoff.schema.json"
DEFAULT_MINIMUM_SPACING_M = {
    "object_to_object": 0.002,
    "object_to_frame": 0.005,
    "text_to_geometry": 0.001,
}


class DrawingLayoutHandoffError(ValueError):
    """Raised when a G1 request or published handoff fails closed."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dimension_invariant_rows(sidecar: Mapping[str, Any]) -> list[dict[str, Any]]:
    reopen = sidecar.get("reopen_verification")
    rows = reopen.get("dimensions") if isinstance(reopen, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise DrawingLayoutHandoffError(
            "dimension verification sidecar has no reopened dimension readback"
        )
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise DrawingLayoutHandoffError(f"invalid reopened dimension row {index}")
        dimension_id = row.get("dimension_id")
        value = row.get("value_si")
        references = row.get("model_persistent_references")
        if (
            not isinstance(dimension_id, str)
            or not dimension_id
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isinstance(references, list)
            or not references
            or any(not isinstance(item, str) or not item for item in references)
        ):
            raise DrawingLayoutHandoffError(
                f"reopened dimension row {index} lacks immutable value/attachment data"
            )
        result.append(
            {
                "dimension_id": dimension_id,
                "value_si": value,
                "model_persistent_references": list(references),
            }
        )
    if len({row["dimension_id"] for row in result}) != len(result):
        raise DrawingLayoutHandoffError("reopened dimension IDs must be unique")
    return sorted(result, key=lambda row: row["dimension_id"])


def dimension_invariant_sha256(rows: list[dict[str, Any]]) -> str:
    return canonical_json_sha256(rows, "dimension invariants")


def build_layout_handoff_request(
    dimension_plan_path: Path,
    dimensioned_drawing_path: Path,
    dimension_verification_sidecar_path: Path,
    publication_directory: Path,
    *,
    capability_manifest_path: Path = CAPABILITY_MANIFEST_PATH,
    minimum_spacing_m: Mapping[str, float] = DEFAULT_MINIMUM_SPACING_M,
) -> dict[str, Any]:
    plan_path = _absolute_file(dimension_plan_path, ".json", "DimensionPlan")
    drawing_path = _absolute_file(
        dimensioned_drawing_path, ".slddrw", "dimensioned drawing"
    )
    sidecar_path = _absolute_file(
        dimension_verification_sidecar_path, ".json", "dimension verification sidecar"
    )
    manifest_path = _absolute_file(
        capability_manifest_path, ".json", "layout capability manifest"
    )
    publication = publication_directory.resolve()
    if publication.exists():
        raise DrawingLayoutHandoffError(
            f"publication_directory must be a new path: {publication}"
        )
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if publication == validation_root or validation_root in publication.parents:
        raise DrawingLayoutHandoffError(
            "publication_directory must not be validation or one of its descendants"
        )
    upstream_paths = (plan_path, drawing_path, sidecar_path, manifest_path)
    if any(publication == path.parent for path in upstream_paths):
        raise DrawingLayoutHandoffError(
            "publication_directory must differ from every upstream artifact directory"
        )

    plan = _load_json(plan_path)
    sidecar = _load_json(sidecar_path)
    manifest = _load_json(manifest_path)
    if plan.get("protocol_id") != "solidworks-dimension-plan" or plan.get(
        "schema_version"
    ) != "1.0":
        raise DrawingLayoutHandoffError("upstream plan is not DimensionPlan 1.0")
    if sidecar.get("protocol_id") != "solidworks-dimension-drawing-verification" or (
        sidecar.get("schema_version") != "1.0" or sidecar.get("verified") is not True
    ):
        raise DrawingLayoutHandoffError("dimension verification sidecar is not verified")
    plan_hash = file_sha256(plan_path)
    drawing_hash = file_sha256(drawing_path)
    if _resolved_path(sidecar.get("plan_file_path")) != plan_path:
        raise DrawingLayoutHandoffError("sidecar plan_file_path does not match DimensionPlan")
    if sidecar.get("plan_file_sha256") != plan_hash:
        raise DrawingLayoutHandoffError("sidecar plan_file_sha256 does not match DimensionPlan")
    if sidecar.get("plan_canonical_sha256") != canonical_json_sha256(
        plan, "DimensionPlan"
    ):
        raise DrawingLayoutHandoffError("sidecar canonical plan hash does not match")
    if _resolved_path(sidecar.get("output_path")) != drawing_path or sidecar.get(
        "artifact_sha256"
    ) != drawing_hash:
        raise DrawingLayoutHandoffError("sidecar does not bind the dimensioned drawing")
    if sidecar.get("plan_id") != plan.get("plan_id"):
        raise DrawingLayoutHandoffError("sidecar plan_id does not match DimensionPlan")
    if not (
        sidecar.get("in_memory_verification", {}).get("verified") is True
        and sidecar.get("reopen_verification", {}).get("verified") is True
    ):
        raise DrawingLayoutHandoffError("dimension drawing lacks independent reopen verification")
    invariants = dimension_invariant_rows(sidecar)
    planned_ids = [row.get("dimension_id") for row in plan.get("dimensions", [])]
    if len(planned_ids) != len(invariants) or set(planned_ids) != {
        row["dimension_id"] for row in invariants
    }:
        raise DrawingLayoutHandoffError(
            "planned dimension IDs/count differ from verified reopened dimensions"
        )

    if (
        manifest.get("protocol_id")
        != "solidworks-drawing-layout-executor-capabilities"
        or manifest.get("schema_version") != "1.0"
        or manifest.get("verification") != "live_complete"
    ):
        raise DrawingLayoutHandoffError("layout capability manifest is not live_complete")
    live = manifest.get("live_evidence")
    if not isinstance(live, Mapping):
        raise DrawingLayoutHandoffError("layout capability manifest lacks live evidence")
    qualification_path = _absolute_file(
        Path(str(live.get("qualification_path", ""))),
        ".json",
        "G0 qualification",
    )
    qualification_hash = file_sha256(qualification_path)
    if qualification_hash != live.get("qualification_sha256"):
        raise DrawingLayoutHandoffError("G0 qualification SHA-256 binding is stale")
    qualification = _load_json(qualification_path)
    if (
        qualification.get("protocol_id") != "solidworks-layout-g0-qualification"
        or qualification.get("overall_status") != "complete"
        or qualification.get("qualification_id") != live.get("qualification_id")
        or qualification.get("solidworks_revision") != manifest.get("solidworks_revision")
    ):
        raise DrawingLayoutHandoffError("G0 qualification does not bind the capability manifest")

    request = {
        "protocol_id": "solidworks-drawing-layout-handoff-request",
        "schema_version": "1.0",
        "source": {
            "dimension_plan": {"path": str(plan_path), "sha256": plan_hash},
            "dimensioned_drawing": {"path": str(drawing_path), "sha256": drawing_hash},
            "dimension_verification_sidecar": {
                "path": str(sidecar_path),
                "sha256": file_sha256(sidecar_path),
            },
        },
        "boundary_capabilities": {
            "manifest": {"path": str(manifest_path), "sha256": file_sha256(manifest_path)},
            "qualification": {
                "path": str(qualification_path),
                "sha256": qualification_hash,
            },
        },
        "publication_directory": str(publication),
        "minimum_spacing_m": dict(minimum_spacing_m),
    }
    return validate_layout_handoff_request(request)


def validate_layout_handoff_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_object_copy(candidate, "layout handoff request")
    request_schema, _ = _contracts()
    _validate_json(normalized, request_schema, "layout handoff request")
    return normalized


def validate_drawing_layout_handoff(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_object_copy(candidate, "drawing layout handoff")
    request_schema, handoff_schema = _contracts()
    registry = Registry().with_resource(
        request_schema["$id"], Resource.from_contents(request_schema)
    )
    _validate_json(
        normalized, handoff_schema, "drawing layout handoff", registry=registry
    )
    rows = normalized["upstream_artifacts"]
    expected_roles = {
        "dimension_plan",
        "dimensioned_drawing",
        "dimension_verification_sidecar",
        "boundary_capability_manifest",
        "boundary_qualification",
    }
    if len(rows) != len(expected_roles) or {row["role"] for row in rows} != expected_roles:
        raise DrawingLayoutHandoffError(
            "upstream_artifacts must contain exactly the five frozen G1 roles"
        )
    if any(row["sha256_before"] != row["sha256_after"] for row in rows):
        raise DrawingLayoutHandoffError("an upstream artifact changed during G1")
    objects = normalized["objects"]
    ids = [row["id"] for row in objects]
    if len(ids) != len(set(ids)):
        raise DrawingLayoutHandoffError("layout boundary object IDs must be unique")
    for row in objects:
        left, bottom, right, top = row["bounds"]
        if left > right or bottom > top:
            raise DrawingLayoutHandoffError(f"non-normalized bounds for {row['id']}")
        if row["collision_usable"] != bool(row["exact"]):
            raise DrawingLayoutHandoffError(
                f"collision usability must exactly track qualified exactness: {row['id']}"
            )
    dimensions = normalized["dimension_semantics"]["dimensions"]
    if normalized["dimension_semantics"]["invariant_sha256"] != dimension_invariant_sha256(
        dimensions
    ):
        raise DrawingLayoutHandoffError("dimension invariant SHA-256 does not match rows")
    blocked = bool(normalized["boundary_capabilities"]["unsupported"])
    if (normalized["status"] == "capability_blocked") != blocked:
        raise DrawingLayoutHandoffError(
            "handoff status must reflect unsupported required boundary capabilities"
        )
    if blocked != bool(normalized["blockers"]):
        raise DrawingLayoutHandoffError("capability blockers must be explicit")
    return normalized


def _contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    return _load_json(REQUEST_CONTRACT_PATH), _load_json(HANDOFF_CONTRACT_PATH)


def _absolute_file(path: Path, suffix: str, label: str) -> Path:
    resolved = path.resolve()
    if any(character in str(path) for character in ("*", "?", "[", "]")) or (
        not resolved.is_file() or resolved.suffix.lower() != suffix
    ):
        raise DrawingLayoutHandoffError(
            f"{label} must be an existing {suffix} file: {resolved}"
        )
    return resolved


def _resolved_path(value: object) -> Path | None:
    return Path(value).resolve() if isinstance(value, str) and value.strip() else None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrawingLayoutHandoffError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DrawingLayoutHandoffError(f"JSON artifact must contain an object: {path}")
    return value


def _json_object_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise DrawingLayoutHandoffError(f"{label} must be an object")
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DrawingLayoutHandoffError(f"{label} is not strict JSON: {exc}") from exc
    return value


def _validate_json(
    candidate: dict[str, Any],
    schema: dict[str, Any],
    label: str,
    *,
    registry: Registry | None = None,
) -> None:
    validator = Draft202012Validator(
        schema, format_checker=FormatChecker(), registry=registry or Registry()
    )
    errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise DrawingLayoutHandoffError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )
