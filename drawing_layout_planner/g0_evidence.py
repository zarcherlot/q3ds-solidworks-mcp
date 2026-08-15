"""Deterministic G0 annotation-boundary and rebuild-drift evidence gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "layout-boundary-evidence.schema.json"
CAPABILITY_PATH = PACKAGE_ROOT / "capabilities" / "current.json"
REQUIRED_SOLIDWORKS_REVISION = "33.5.0"
G0_CAPABILITY_IDS = (
    "view_outline_bounds",
    "dimension_display_bounds",
    "note_text_bounds",
    "leader_bounds",
    "view_label_bounds",
    "section_symbol_bounds",
    "center_element_bounds",
    "sheet_border_bounds",
    "title_block_bounds",
    "rebuild_drift",
    "save_reopen_drift",
)
DIMENSION_UPSTREAM_ROLES = (
    "dimension_plan",
    "dimensioned_drawing",
    "dimension_verification_sidecar",
)
VIEW_UPSTREAM_ROLES = (
    "view_plan",
    "view_drawing",
    "view_verification_sidecar",
)
FIXTURE_UPSTREAM_ROLES = (
    "layout_fixture_manifest",
    "fixture_drawing",
    "source_verification_sidecar",
)


class G0BoundaryEvidenceError(ValueError):
    """Raised when a G0 report violates a frozen contract or evidence gate."""


@dataclass(frozen=True)
class G0Evaluation:
    evidence_sha256: str
    overall_status: str
    capability_statuses: Mapping[str, str]
    blockers: tuple[str, ...]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise G0BoundaryEvidenceError(f"JSON root must be an object: {path}")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_g0_capability_manifest(
    path: Path | str = CAPABILITY_PATH,
) -> dict[str, Any]:
    manifest = _load_json(Path(path))
    if manifest.get("protocol_id") != "solidworks-drawing-layout-executor-capabilities":
        raise G0BoundaryEvidenceError("unexpected layout capability protocol")
    rows = manifest.get("capabilities")
    if not isinstance(rows, list):
        raise G0BoundaryEvidenceError("layout capability manifest must contain capabilities")
    if [row.get("id") for row in rows if isinstance(row, dict)] != list(
        G0_CAPABILITY_IDS
    ):
        raise G0BoundaryEvidenceError(
            "layout capability manifest must match the frozen G0 catalog and order"
        )
    if any(row.get("status") not in {"supported", "planned", "unsupported"} for row in rows):
        raise G0BoundaryEvidenceError("layout capability manifest has an invalid status")
    if any(row.get("status") == "supported" for row in rows) and not isinstance(
        manifest.get("live_evidence"), dict
    ):
        raise G0BoundaryEvidenceError(
            "supported layout capabilities require bound live_evidence"
        )
    return manifest


def evaluate_g0_evidence(
    evidence: Mapping[str, Any],
    *,
    contract_path: Path | str = CONTRACT_PATH,
    capability_path: Path | str = CAPABILITY_PATH,
    source_request: Mapping[str, Any] | None = None,
) -> G0Evaluation:
    if not isinstance(evidence, Mapping):
        raise G0BoundaryEvidenceError("evidence must be an object")
    validator = Draft202012Validator(
        _load_json(Path(contract_path)), format_checker=FormatChecker()
    )
    errors = sorted(validator.iter_errors(evidence), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise G0BoundaryEvidenceError(f"schema failure at {pointer}: {first.message}")

    if source_request is not None:
        if not isinstance(source_request, Mapping):
            raise G0BoundaryEvidenceError("source_request must be an object")
        if evidence["source_request_sha256"] != _canonical_sha256(source_request):
            raise G0BoundaryEvidenceError(
                "source_request_sha256 does not bind the supplied G0 request"
            )

    load_g0_capability_manifest(capability_path)
    rows = evidence["capabilities"]
    if [row["id"] for row in rows] != list(G0_CAPABILITY_IDS):
        raise G0BoundaryEvidenceError(
            "capability evidence must follow the complete G0 manifest order"
        )
    upstream = evidence["upstream_immutability"]
    expected_roles = {
        "verified_dimension_drawing": DIMENSION_UPSTREAM_ROLES,
        "verified_view_plan_drawing": VIEW_UPSTREAM_ROLES,
        "verified_layout_fixture": FIXTURE_UPSTREAM_ROLES,
    }[evidence["source_kind"]]
    if [row["role"] for row in upstream] != list(expected_roles):
        raise G0BoundaryEvidenceError(
            "upstream evidence must match the frozen G0 artifact order"
        )
    immutable = all(
        row["sha256_before"].lower() == row["sha256_after"].lower()
        for row in upstream
    )
    sw = evidence["solidworks"]
    revision_ok = (
        sw["major_version"] == 2025
        and sw["service_pack"] == "SP5"
        and sw["revision"] == REQUIRED_SOLIDWORKS_REVISION
    )

    blockers: list[str] = []
    statuses: dict[str, str] = {}
    for row in rows:
        capability_id = row["id"]
        checks = row["checks"]
        supported_ready = (
            evidence["execution_mode"] == "live"
            and revision_ok
            and immutable
            and checks["native_api_invoked"]
            and checks["objects_observed"]
            and checks["bounds_structured"]
            and checks["rebuild_compared"]
            and checks["save_reopen_compared"]
            and checks["within_error_budget"]
            and row["max_drift_m"] is not None
            and row["max_drift_m"] <= evidence["error_budget_m"]
            and bool(row["evidence"])
        )
        unsupported_ready = (
            evidence["execution_mode"] == "live"
            and revision_ok
            and immutable
            and checks["native_api_invoked"]
            and bool(row["evidence"])
            and bool(row["limitations"])
        )
        if row["status"] == "supported" and not supported_ready:
            statuses[capability_id] = "planned"
            blockers.append(
                capability_id
                + ": supported requires observed bounds and stable rebuild/reopen evidence"
            )
        elif row["status"] == "unsupported" and not unsupported_ready:
            statuses[capability_id] = "planned"
            blockers.append(
                capability_id
                + ": unsupported requires live native evidence and a stable limitation"
            )
        else:
            statuses[capability_id] = row["status"]

    if not immutable:
        blockers.append("one or more frozen upstream drawing artifacts changed")
    if evidence["execution_mode"] == "live" and not revision_ok:
        blockers.append("live evidence revision must be " + REQUIRED_SOLIDWORKS_REVISION)

    if blockers:
        overall = "capability_blocked"
    elif (
        evidence["execution_mode"] == "live"
        and revision_ok
        and immutable
        and all(value in {"supported", "unsupported"} for value in statuses.values())
    ):
        overall = "complete"
    else:
        overall = "incomplete"
    return G0Evaluation(
        evidence_sha256=_canonical_sha256(evidence),
        overall_status=overall,
        capability_statuses=statuses,
        blockers=tuple(blockers),
    )
