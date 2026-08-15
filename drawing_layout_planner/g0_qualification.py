"""Final fail-closed G0 qualification and capability-registry promotion."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .g0_evidence import G0_CAPABILITY_IDS, evaluate_g0_evidence
from .g0_matrix import file_sha256


SCHEMA_PATH = Path(__file__).resolve().parent / "contracts" / "layout-boundary-qualification.schema.json"


class G0QualificationError(ValueError):
    pass


def _load(path: Path | str) -> dict[str, Any]:
    source = Path(path).resolve(strict=True)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise G0QualificationError(f"JSON root must be an object: {source}")
    return value


def _artifact(path: Path | str) -> dict[str, str]:
    source = Path(path).resolve(strict=True)
    return {"path": str(source), "sha256": file_sha256(source)}


def _approximation_qualified(row: dict[str, Any], budget: float) -> bool:
    checks = row["checks"]
    return (
        row["status"] == "planned"
        and checks["native_api_invoked"]
        and checks["objects_observed"]
        and checks["bounds_structured"]
        and checks["rebuild_compared"]
        and checks["save_reopen_compared"]
        and checks["within_error_budget"]
        and row["max_drift_m"] is not None
        and row["max_drift_m"] <= budget
        and bool(row["evidence"])
        and any("approximation" in item.lower() for item in row["limitations"])
    )


def build_g0_qualification(
    base_matrix_path: Path | str,
    supplemental_evidence_paths: Iterable[Path | str],
    *,
    qualification_id: str,
) -> dict[str, Any]:
    matrix_path = Path(base_matrix_path).resolve(strict=True)
    matrix = _load(matrix_path)
    if matrix.get("protocol_id") != "solidworks-layout-g0-matrix-summary":
        raise G0QualificationError("base matrix has an unexpected protocol")
    evidence_paths: list[Path] = []
    for case in matrix["case_results"]:
        path = Path(case["evidence_path"]).resolve(strict=True)
        if file_sha256(path) != case["evidence_file_sha256"]:
            raise G0QualificationError("base matrix evidence hash drift: " + str(path))
        evidence_paths.append(path)
    supplemental = [Path(path).resolve(strict=True) for path in supplemental_evidence_paths]
    if len(supplemental) < 3 or len(set(supplemental)) != len(supplemental):
        raise G0QualificationError("at least three distinct supplemental evidence files are required")
    evidence_paths.extend(supplemental)

    evidence_rows: list[tuple[Path, dict[str, Any]]] = []
    for path in evidence_paths:
        evidence = _load(path)
        evaluation = evaluate_g0_evidence(evidence)
        if evaluation.blockers:
            raise G0QualificationError("blocked evidence: " + str(path))
        evidence_rows.append((path, evidence))

    capabilities: list[dict[str, Any]] = []
    for capability_id in G0_CAPABILITY_IDS:
        supported: list[tuple[Path, dict[str, Any]]] = []
        approximated: list[tuple[Path, dict[str, Any]]] = []
        for path, evidence in evidence_rows:
            row = next(item for item in evidence["capabilities"] if item["id"] == capability_id)
            if row["status"] == "supported":
                supported.append((path, row))
            elif _approximation_qualified(row, float(evidence["error_budget_m"])):
                approximated.append((path, row))
        selected = supported or approximated
        if not selected:
            raise G0QualificationError("capability remains unqualified: " + capability_id)
        drifts = [float(row["max_drift_m"]) for _, row in selected if row["max_drift_m"] is not None]
        limitation = None
        status = "supported"
        if not supported:
            status = "unsupported"
            limitation = "SolidWorks native display data exposes stable anchors/geometry but no exact text glyph extent; deterministic approximation is not promoted as an exact boundary."
        capabilities.append(
            {
                "id": capability_id,
                "status": status,
                "evidence_paths": [str(path) for path, _ in selected],
                "max_drift_m": max(drifts) if drifts else None,
                "limitation": limitation,
            }
        )
    result = {
        "protocol_id": "solidworks-layout-g0-qualification",
        "schema_version": "1.0",
        "qualification_id": qualification_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "solidworks_revision": "33.5.0",
        "base_matrix": _artifact(matrix_path),
        "supplemental_evidence": [_artifact(path) for path in supplemental],
        "capabilities": capabilities,
        "overall_status": "complete",
    }
    schema = _load(SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result),
        key=lambda item: list(item.path),
    )
    if errors:
        raise G0QualificationError(errors[0].message)
    return result


def promoted_capability_manifest(
    current: dict[str, Any], qualification_path: Path | str
) -> dict[str, Any]:
    path = Path(qualification_path).resolve(strict=True)
    qualification = _load(path)
    if qualification.get("overall_status") != "complete":
        raise G0QualificationError("only a complete qualification can promote the registry")
    promoted = copy.deepcopy(current)
    current_version = str(current.get("registry_version", "0.0.0"))
    try:
        major, minor, _patch = (int(item) for item in current_version.split("."))
    except (TypeError, ValueError):
        raise G0QualificationError("current capability registry has an invalid version")
    promoted["registry_version"] = (
        "1.0.0" if major < 1 else f"{major}.{minor + 1}.0"
    )
    promoted["verification"] = "live_complete"
    promoted["capabilities"] = [
        {"id": row["id"], "status": row["status"]}
        for row in qualification["capabilities"]
    ]
    promoted["live_evidence"] = {
        "qualification_path": str(path),
        "qualification_sha256": file_sha256(path),
        "qualification_id": qualification["qualification_id"],
        "solidworks_revision": qualification["solidworks_revision"],
    }
    return promoted
