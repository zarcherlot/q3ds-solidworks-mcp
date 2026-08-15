"""Immutable six-category G0 live-matrix construction and aggregation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from .g0_evidence import G0_CAPABILITY_IDS, evaluate_g0_evidence


PACKAGE_ROOT = Path(__file__).resolve().parent
REQUEST_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "layout-boundary-matrix-request.schema.json"
SUMMARY_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "layout-boundary-matrix-summary.schema.json"
G0_MATRIX_CATEGORIES = (
    "plate",
    "bracket",
    "threaded",
    "shaft_sleeve",
    "flange",
    "slot_cavity",
)


class G0MatrixError(ValueError):
    """Raised when matrix inputs, evidence, or publication are not immutable."""


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise G0MatrixError(f"JSON root must be an object: {source}")
    return value


def _validate_schema(value: Mapping[str, Any], schema_path: Path) -> None:
    validator = Draft202012Validator(
        _load_json(schema_path), format_checker=FormatChecker()
    )
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise G0MatrixError(f"schema failure at {pointer}: {first.message}")


def build_probe_request(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": "solidworks-layout-boundary-probe",
        "schema_version": "1.0",
        "source": json.loads(json.dumps(case["source"], ensure_ascii=False)),
        "publication_directory": case["publication_directory"],
        "required_solidworks_revision": "33.5.0",
        "error_budget_m": case["error_budget_m"],
        "capability_ids": list(G0_CAPABILITY_IDS),
    }


def validate_matrix_request(
    value: Mapping[str, Any], *, allow_case_outputs: bool = False
) -> None:
    if not isinstance(value, Mapping):
        raise G0MatrixError("matrix request must be an object")
    _validate_schema(value, REQUEST_SCHEMA_PATH)
    cases = value["cases"]
    categories = tuple(case["category"] for case in cases)
    if categories != G0_MATRIX_CATEGORIES:
        raise G0MatrixError("matrix categories must match the frozen six-category order")
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise G0MatrixError("matrix case IDs must be unique")
    publications: set[str] = set()
    for case in cases:
        publication = Path(case["publication_directory"])
        if not publication.is_absolute():
            raise G0MatrixError("case publication directories must be absolute")
        resolved_publication = str(publication.resolve()).lower()
        if "validation" in {part.lower() for part in publication.resolve().parts}:
            raise G0MatrixError("matrix output may not be written under validation")
        if resolved_publication in publications:
            raise G0MatrixError("case publication directories must be distinct")
        publications.add(resolved_publication)
        case_source_paths: set[str] = set()
        case_source_directories: set[str] = set()
        for role, artifact in case["source"].items():
            if role == "kind":
                continue
            path = Path(artifact["path"])
            if not path.is_absolute() or not path.is_file():
                raise G0MatrixError(f"missing absolute source artifact: {path}")
            if file_sha256(path) != artifact["sha256"]:
                raise G0MatrixError(f"source artifact SHA-256 mismatch: {path}")
            resolved_source = path.resolve()
            case_source_paths.add(str(resolved_source).lower())
            case_source_directories.add(str(resolved_source.parent).lower())
        if resolved_publication in case_source_paths:
            raise G0MatrixError("case publication directory aliases a source artifact")
        if resolved_publication in case_source_directories:
            raise G0MatrixError("case publication directory aliases a source directory")
        if (
            not allow_case_outputs
            and publication.exists()
            and any(publication.iterdir())
        ):
            raise G0MatrixError(
                f"case publication directory must be new or empty: {publication}"
            )


def build_matrix_request_from_f7(
    evidence_directory: Path | str,
    matrix_root: Path | str,
    *,
    matrix_id: str,
    error_budget_m: float = 0.0005,
) -> dict[str, Any]:
    evidence_root = Path(evidence_directory).resolve(strict=True)
    publication_root = Path(matrix_root).resolve()
    if "validation" in {part.lower() for part in publication_root.parts}:
        raise G0MatrixError("matrix root may not be under validation")
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(evidence_root.glob("*.evidence.json")):
        evidence = _load_json(path)
        if evidence.get("protocol_id") != "solidworks-dimension-f7-case-evidence":
            continue
        category = evidence.get("category")
        if category not in G0_MATRIX_CATEGORIES or category in rows:
            raise G0MatrixError("F7 evidence categories must be known and unique")
        invariants = evidence.get("invariants")
        if not isinstance(invariants, dict) or not all(
            invariants.get(name) is True
            for name in (
                "source_hashes_unchanged",
                "save_close_readonly_reopen",
                "independent_readonly_verify",
                "persisted_fingerprint_match",
            )
        ):
            raise G0MatrixError(f"F7 evidence is not independently verified: {path}")
        plan = evidence["plan"]
        output = evidence["output"]
        artifacts = {
            "dimension_plan": {"path": plan["path"], "sha256": plan["file_sha256"]},
            "dimensioned_drawing": {"path": output["path"], "sha256": output["sha256"]},
            "dimension_verification_sidecar": {
                "path": output["verification_sidecar_path"],
                "sha256": output["verification_sidecar_sha256"],
            },
        }
        for artifact in artifacts.values():
            if file_sha256(artifact["path"]) != artifact["sha256"]:
                raise G0MatrixError(
                    "F7 evidence artifact hash drift: " + artifact["path"]
                )
        rows[category] = {
            "case_id": "G0-" + evidence["case_id"],
            "category": category,
            "source": {"kind": "verified_dimension_drawing", **artifacts},
            "publication_directory": str(publication_root / "cases" / category),
            "error_budget_m": error_budget_m,
        }
    if set(rows) != set(G0_MATRIX_CATEGORIES):
        missing = sorted(set(G0_MATRIX_CATEGORIES) - set(rows))
        raise G0MatrixError("F7 evidence is missing categories: " + ", ".join(missing))
    request = {
        "protocol_id": "solidworks-layout-g0-matrix-request",
        "schema_version": "1.0",
        "matrix_id": matrix_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "required_solidworks_revision": "33.5.0",
        "cases": [rows[category] for category in G0_MATRIX_CATEGORIES],
    }
    validate_matrix_request(request)
    return request


def build_matrix_summary(
    request: Mapping[str, Any],
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    validate_matrix_request(request, allow_case_outputs=True)
    if set(reports) != {case["case_id"] for case in request["cases"]}:
        raise G0MatrixError("matrix reports must match every requested case exactly")
    blockers: list[str] = []
    case_results: list[dict[str, Any]] = []
    evidence_by_case: dict[str, Mapping[str, Any]] = {}
    for case in request["cases"]:
        case_id = case["case_id"]
        report = reports[case_id]
        evidence = report.get("evidence")
        if not isinstance(evidence, Mapping):
            raise G0MatrixError(f"case report has no evidence object: {case_id}")
        probe_request = build_probe_request(case)
        evaluation = evaluate_g0_evidence(evidence, source_request=probe_request)
        blockers.extend(f"{case_id}: {item}" for item in evaluation.blockers)
        evidence_path = Path(str(report["evidence_path"])).resolve(strict=True)
        file_hash = file_sha256(evidence_path)
        if file_hash != report["evidence_file_sha256"]:
            raise G0MatrixError(f"case evidence file hash drift: {case_id}")
        persisted_evidence = _load_json(evidence_path)
        if canonical_sha256(persisted_evidence) != canonical_sha256(evidence):
            raise G0MatrixError(
                f"case evidence object differs from its persisted file: {case_id}"
            )
        evidence_by_case[case_id] = evidence
        case_results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "source_request_sha256": evidence["source_request_sha256"],
                "evidence_path": str(evidence_path),
                "evidence_file_sha256": file_hash,
                "evidence_canonical_sha256": evaluation.evidence_sha256,
                "overall_status": evaluation.overall_status,
            }
        )

    coverage: list[dict[str, Any]] = []
    for capability_id in G0_CAPABILITY_IDS:
        observed: list[str] = []
        supported: list[str] = []
        drift_values: list[float] = []
        for case in request["cases"]:
            case_id = case["case_id"]
            row = next(
                item
                for item in evidence_by_case[case_id]["capabilities"]
                if item["id"] == capability_id
            )
            if row["checks"]["objects_observed"]:
                observed.append(case_id)
            if row["status"] == "supported":
                supported.append(case_id)
            if row["max_drift_m"] is not None:
                drift_values.append(float(row["max_drift_m"]))
        status = "covered" if supported else ("partial" if observed else "missing")
        coverage.append(
            {
                "id": capability_id,
                "status": status,
                "observed_case_ids": observed,
                "supported_case_ids": supported,
                "max_drift_m": max(drift_values) if drift_values else None,
            }
        )
    if blockers:
        overall_status = "capability_blocked"
    elif all(row["status"] == "covered" for row in coverage):
        overall_status = "complete"
    else:
        overall_status = "incomplete"
    summary = {
        "protocol_id": "solidworks-layout-g0-matrix-summary",
        "schema_version": "1.0",
        "matrix_id": request["matrix_id"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "matrix_request_sha256": canonical_sha256(request),
        "case_results": case_results,
        "coverage": coverage,
        "overall_status": overall_status,
        "blockers": blockers,
    }
    _validate_schema(summary, SUMMARY_SCHEMA_PATH)
    return summary


def publish_json_once(path: Path | str, value: Mapping[str, Any]) -> str:
    target = Path(path).resolve()
    if target.exists():
        raise G0MatrixError(f"refusing to overwrite immutable publication: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".tmp-", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return file_sha256(target)
