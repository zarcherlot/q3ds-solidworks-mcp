"""Strict F1 request construction and immutable handoff validation.

SolidWorks readback belongs to the C# execution service.  This module only
normalizes paths, binds already-verified upstream artifacts, validates the two
F1 JSON contracts, and checks the published immutability ledger.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from drawing_planner.planning_models import canonical_json_sha256


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_ROOT = PACKAGE_ROOT / "contracts"
REQUEST_CONTRACT_PATH = CONTRACT_ROOT / "dimension-planning-handoff-request.schema.json"
HANDOFF_CONTRACT_PATH = CONTRACT_ROOT / "dimension-planning-handoff.schema.json"


class DimensionPlanningHandoffError(ValueError):
    """Raised when an F1 request or published handoff fails closed."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_handoff_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    return (_load_json(REQUEST_CONTRACT_PATH), _load_json(HANDOFF_CONTRACT_PATH))


def validate_handoff_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_object_copy(candidate, "dimension handoff request")
    request_schema, _ = load_handoff_contracts()
    _validate_json(normalized, request_schema, "dimension handoff request")
    return normalized


def validate_dimension_planning_handoff(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _json_object_copy(candidate, "dimension planning handoff")
    request_schema, handoff_schema = load_handoff_contracts()
    registry = Registry().with_resource(
        request_schema["$id"], Resource.from_contents(request_schema)
    )
    _validate_json(
        normalized,
        handoff_schema,
        "dimension planning handoff",
        registry=registry,
    )

    artifacts = normalized["upstream_artifacts"]
    roles = [row["role"] for row in artifacts]
    required_roles = {"view_plan", "verified_drawing", "verification_sidecar", "source_model"}
    if set(roles) != required_roles or len(roles) != len(required_roles):
        raise DimensionPlanningHandoffError(
            "upstream_artifacts must contain exactly one row for each frozen role"
        )
    for row in artifacts:
        if row["sha256_before"] != row["sha256_after"]:
            raise DimensionPlanningHandoffError(
                f"upstream artifact changed during handoff: {row['role']}"
            )
    if normalized["source_model"]["sha256"] != next(
        row["sha256_before"] for row in artifacts if row["role"] == "source_model"
    ):
        raise DimensionPlanningHandoffError(
            "source_model SHA-256 does not match the immutability ledger"
        )
    return normalized


def build_handoff_request(
    view_plan_path: Path,
    verified_drawing_path: Path,
    verification_sidecar_path: Path,
    publication_directory: Path,
    *,
    approved_user_inputs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    view_plan = _absolute_file(view_plan_path, ".json", "ViewPlan")
    drawing = _absolute_file(verified_drawing_path, ".slddrw", "verified drawing")
    sidecar = _absolute_file(
        verification_sidecar_path, ".json", "verification sidecar"
    )
    publication = publication_directory.resolve()
    if publication.exists():
        raise DimensionPlanningHandoffError(
            f"publication_directory must be a new path: {publication}"
        )
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if publication == validation_root or validation_root in publication.parents:
        raise DimensionPlanningHandoffError(
            "publication_directory must not be validation or one of its descendants"
        )
    for upstream in (view_plan, drawing, sidecar):
        if publication == upstream.parent:
            raise DimensionPlanningHandoffError(
                "publication_directory must not be the upstream artifact directory"
            )

    plan_value = _load_json(view_plan)
    sidecar_value = _load_json(sidecar)
    drawing_hash = file_sha256(drawing)
    model = _absolute_file(Path(plan_value.get("model_path", "")), ".sldprt", "source model")
    model_hash = file_sha256(model)
    if plan_value.get("protocol_id") != "solidworks-view-plan" or plan_value.get(
        "schema_version"
    ) != "1.4":
        raise DimensionPlanningHandoffError("upstream plan is not ViewPlan 1.4")
    if plan_value.get("model_sha256") != model_hash:
        raise DimensionPlanningHandoffError(
            "ViewPlan model_sha256 does not match the source model"
        )
    if sidecar_value.get("verified") is not True:
        raise DimensionPlanningHandoffError("verification sidecar is not verified")
    if _resolved_path(sidecar_value.get("output_path")) != drawing:
        raise DimensionPlanningHandoffError(
            "verification sidecar output_path does not match the drawing"
        )
    if sidecar_value.get("artifact_sha256") != drawing_hash:
        raise DimensionPlanningHandoffError(
            "verification sidecar artifact_sha256 does not match the drawing"
        )
    plan_hash = canonical_json_sha256(plan_value, "ViewPlan")
    if sidecar_value.get("plan_canonical_sha256") != plan_hash:
        raise DimensionPlanningHandoffError(
            "verification sidecar plan hash does not match the ViewPlan"
        )

    request = {
        "protocol_id": "solidworks-dimension-planning-handoff-request",
        "schema_version": "1.0",
        "source": {
            "view_plan": {"path": str(view_plan), "sha256": file_sha256(view_plan)},
            "verified_drawing": {"path": str(drawing), "sha256": drawing_hash},
            "verification_sidecar": {
                "path": str(sidecar),
                "sha256": file_sha256(sidecar),
            },
        },
        "publication_directory": str(publication),
        "approved_user_inputs": [copy.deepcopy(dict(item)) for item in approved_user_inputs],
    }
    return validate_handoff_request(request)


def _absolute_file(path: Path, suffix: str, label: str) -> Path:
    resolved = path.resolve()
    if any(character in str(path) for character in ("*", "?", "[", "]")) or (
        not resolved.is_file() or resolved.suffix.lower() != suffix
    ):
        raise DimensionPlanningHandoffError(
            f"{label} must be an existing {suffix} file: {resolved}"
        )
    return resolved


def _resolved_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DimensionPlanningHandoffError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionPlanningHandoffError(f"JSON artifact must contain an object: {path}")
    return value


def _json_object_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise DimensionPlanningHandoffError(f"{label} must be an object")
    try:
        normalized = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DimensionPlanningHandoffError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(normalized, dict):
        raise DimensionPlanningHandoffError(f"{label} must be an object")
    return normalized


def _validate_json(
    candidate: dict[str, Any],
    schema: dict[str, Any],
    label: str,
    *,
    registry: Registry | None = None,
) -> None:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=registry or Registry(),
    )
    errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise DimensionPlanningHandoffError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )
