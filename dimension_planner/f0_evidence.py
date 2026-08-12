"""Deterministic F0 native-API evidence evaluation.

This module is deliberately COM-free.  The C# execution service produces the
live evidence; Python only validates the immutable report and decides whether
the repository capability manifest may honestly promote an item to
``supported``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "dimension-api-evidence.schema.json"
CAPABILITY_PATH = PACKAGE_ROOT / "capabilities" / "current.json"
REQUIRED_SOLIDWORKS_REVISION = "33.5.0"
F0_CAPABILITY_IDS = (
    "model_dimension_import",
    "display_dimension_iteration",
    "attachment_persistent_reference",
    "annotation_position",
    "annotation_text_bounds",
    "linear_dimension",
    "diameter_dimension",
    "radius_dimension",
    "angular_dimension",
    "hole_callout",
    "chamfer_dimension",
    "dimension_tolerance",
    "dimension_prefix_suffix",
    "save_reopen_stable_identity",
)
REQUIRED_UPSTREAM_ROLES = {
    "research_model_drawing_pair": (
        "source_model",
        "source_drawing",
        "drawing_template",
    ),
    "frozen_viewplan_drawing": (
        "view_plan",
        "verified_drawing",
        "verification_sidecar",
    ),
}


class F0CapabilityEvidenceError(ValueError):
    """Raised when F0 evidence cannot support its declared conclusions."""


@dataclass(frozen=True)
class F0Evaluation:
    evidence_sha256: str
    overall_status: str
    capability_statuses: Mapping[str, str]
    blockers: tuple[str, ...]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise F0CapabilityEvidenceError(f"JSON root must be an object: {path}")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_f0_capability_manifest(
    path: Path | str = CAPABILITY_PATH,
) -> dict[str, Any]:
    manifest = _load_json(Path(path))
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise F0CapabilityEvidenceError("capability manifest must contain capabilities")
    ids = [item.get("id") for item in capabilities if isinstance(item, dict)]
    if ids != list(F0_CAPABILITY_IDS):
        raise F0CapabilityEvidenceError(
            "capability manifest must match the frozen F0 catalog and order"
        )
    allowed_statuses = {"supported", "planned", "unsupported"}
    if any(item.get("status") not in allowed_statuses for item in capabilities):
        raise F0CapabilityEvidenceError("capability manifest contains an invalid status")
    if any(item.get("status") == "supported" for item in capabilities):
        evidence = manifest.get("live_evidence")
        if not isinstance(evidence, dict):
            raise F0CapabilityEvidenceError(
                "supported capabilities require a bound live_evidence object"
            )
    return manifest


def evaluate_f0_evidence(
    evidence: Mapping[str, Any],
    *,
    contract_path: Path | str = CONTRACT_PATH,
    capability_path: Path | str = CAPABILITY_PATH,
    source_request: Mapping[str, Any] | None = None,
) -> F0Evaluation:
    """Validate a report and enforce the F0 promotion gates.

    Schema validity is necessary but not sufficient.  This evaluator also
    enforces the cross-field invariants that JSON Schema cannot express
    compactly: immutable upstream inputs, exact capability coverage, SP5 live
    execution, and per-capability persistence/readback evidence.
    """

    if not isinstance(evidence, Mapping):
        raise F0CapabilityEvidenceError("evidence must be an object")
    contract = _load_json(Path(contract_path))
    validator = Draft202012Validator(contract, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(evidence), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise F0CapabilityEvidenceError(f"schema failure at {pointer}: {first.message}")

    if source_request is not None:
        if not isinstance(source_request, Mapping):
            raise F0CapabilityEvidenceError("source_request must be an object")
        expected_request_hash = _canonical_sha256(source_request)
        if evidence["source_request_sha256"] != expected_request_hash:
            raise F0CapabilityEvidenceError(
                "source_request_sha256 does not bind the supplied probe request"
            )

    manifest = load_f0_capability_manifest(capability_path)
    expected_ids = list(F0_CAPABILITY_IDS)
    rows = evidence["capabilities"]
    actual_ids = [item["id"] for item in rows]
    if actual_ids != expected_ids:
        raise F0CapabilityEvidenceError(
            "capability evidence must follow the complete manifest order"
        )

    upstream = evidence["upstream_immutability"]
    upstream_roles = [item["role"] for item in upstream]
    required_roles = REQUIRED_UPSTREAM_ROLES[evidence["source_kind"]]
    if upstream_roles[: len(required_roles)] != list(required_roles) or len(
        upstream_roles
    ) != len(set(upstream_roles)):
        raise F0CapabilityEvidenceError(
            "upstream evidence must begin with the immutable artifacts for its source kind "
            "and may not repeat roles"
        )
    immutable = all(
        item["sha256_before"].lower() == item["sha256_after"].lower()
        for item in upstream
    )
    solidworks = evidence["solidworks"]
    revision_ok = (
        solidworks["major_version"] == 2025
        and solidworks["service_pack"] == "SP5"
        and solidworks["revision"] == REQUIRED_SOLIDWORKS_REVISION
    )

    blockers: list[str] = []
    statuses: dict[str, str] = {}
    for row in rows:
        capability_id = row["id"]
        declared = row["status"]
        checks = row["checks"]
        promotion_ready = (
            evidence["execution_mode"] == "live"
            and revision_ok
            and immutable
            and checks["native_api_invoked"]
            and checks["in_memory_readback"]
            and checks["save_close_readonly_reopen"]
            and checks["stable_identity"]
            and checks["attachment_readback"]
            and checks["position_readback"]
            and checks["text_bounds_readback"]
        )
        if declared == "supported" and not promotion_ready:
            blockers.append(
                capability_id
                + ": supported requires live SP5 native creation and complete persistence readback"
            )
            statuses[capability_id] = "planned"
        elif declared == "supported" and not row["evidence"]:
            blockers.append(
                capability_id + ": supported requires at least one concrete evidence record"
            )
            statuses[capability_id] = "planned"
        elif declared == "unsupported":
            unsupported_ready = (
                evidence["execution_mode"] == "live"
                and revision_ok
                and immutable
                and checks["native_api_invoked"]
                and bool(row["evidence"])
                and bool(row["limitations"])
            )
            if not unsupported_ready:
                blockers.append(
                    capability_id
                    + ": unsupported requires live SP5 native API evidence and a stable limitation"
                )
                statuses[capability_id] = "planned"
            else:
                statuses[capability_id] = "unsupported"
        else:
            statuses[capability_id] = declared

    if not immutable:
        blockers.append("one or more upstream artifacts changed during the probe")
    if evidence["execution_mode"] == "live" and not revision_ok:
        blockers.append(
            "live evidence revision must be " + REQUIRED_SOLIDWORKS_REVISION
        )
    if blockers:
        overall_status = "capability_blocked"
    elif (
        evidence["execution_mode"] == "live"
        and revision_ok
        and immutable
        and all(status in {"supported", "unsupported"} for status in statuses.values())
    ):
        overall_status = "complete"
    else:
        overall_status = "incomplete"

    return F0Evaluation(
        evidence_sha256=_canonical_sha256(evidence),
        overall_status=overall_status,
        capability_statuses=statuses,
        blockers=tuple(blockers),
    )
