from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from drawing_layout_planner.g0_evidence import G0_CAPABILITY_IDS
from drawing_layout_planner.g0_matrix import (
    G0_MATRIX_CATEGORIES,
    G0MatrixError,
    REQUEST_SCHEMA_PATH,
    SUMMARY_SCHEMA_PATH,
    build_matrix_request_from_f7,
    build_matrix_summary,
    build_probe_request,
    canonical_sha256,
    file_sha256,
    publish_json_once,
    validate_matrix_request,
)


def _artifact(path: Path, content: bytes) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def _request(tmp_path: Path) -> dict:
    cases = []
    for index, category in enumerate(G0_MATRIX_CATEGORIES):
        root = tmp_path / "sources" / category
        cases.append(
            {
                "case_id": f"G0-{category}",
                "category": category,
                "source": {
                    "kind": "verified_dimension_drawing",
                    "dimension_plan": _artifact(
                        root / "dimension_plan.json",
                        json.dumps({"plan_id": f"DP-{category}"}).encode(),
                    ),
                    "dimensioned_drawing": _artifact(
                        root / "dimensioned.SLDDRW", bytes([index + 1])
                    ),
                    "dimension_verification_sidecar": _artifact(
                        root / "dimension.verify.json", b"{}"
                    ),
                },
                "publication_directory": str((tmp_path / "cases" / category).resolve()),
                "error_budget_m": 0.0005,
            }
        )
    return {
        "protocol_id": "solidworks-layout-g0-matrix-request",
        "schema_version": "1.0",
        "matrix_id": "G0-MATRIX-TEST",
        "created_at": "2026-08-14T00:00:00Z",
        "required_solidworks_revision": "33.5.0",
        "cases": cases,
    }


def _evidence(case: dict, *, support_view_bounds: bool = False) -> dict:
    request = build_probe_request(case)
    digest = "a" * 64
    rows = []
    for capability_id in G0_CAPABILITY_IDS:
        supported = support_view_bounds and capability_id == "view_outline_bounds"
        rows.append(
            {
                "id": capability_id,
                "status": "supported" if supported else "planned",
                "checks": {
                    "native_api_invoked": supported,
                    "objects_observed": supported,
                    "bounds_structured": supported,
                    "rebuild_compared": supported,
                    "save_reopen_compared": supported,
                    "within_error_budget": supported,
                },
                "max_drift_m": 0.0 if supported else None,
                "evidence": ["stable view bounds"] if supported else [],
                "limitations": [] if supported else ["not observed"],
            }
        )
    return {
        "protocol_id": "solidworks-layout-boundary-evidence",
        "schema_version": "1.0",
        "probe_id": "LBE-" + case["case_id"],
        "created_at": "2026-08-14T00:00:00Z",
        "execution_mode": "live",
        "source_kind": "verified_dimension_drawing",
        "solidworks": {
            "major_version": 2025,
            "service_pack": "SP5",
            "revision": "33.5.0",
        },
        "source_request_sha256": canonical_sha256(request),
        "error_budget_m": case["error_budget_m"],
        "upstream_immutability": [
            {
                "role": role,
                "path": case["source"][role]["path"],
                "sha256_before": case["source"][role]["sha256"],
                "sha256_after": case["source"][role]["sha256"],
            }
            for role in (
                "dimension_plan",
                "dimensioned_drawing",
                "dimension_verification_sidecar",
            )
        ],
        "snapshots": {
            "before_rebuild_sha256": digest,
            "after_rebuild_sha256": digest,
            "readonly_reopen_sha256": digest,
        },
        "capabilities": rows,
    }


def test_matrix_contracts_are_valid_and_share_catalogs():
    request_schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    summary_schema = json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(request_schema)
    Draft202012Validator.check_schema(summary_schema)
    assert tuple(request_schema["$defs"]["category"]["enum"]) == G0_MATRIX_CATEGORIES
    assert tuple(summary_schema["$defs"]["capabilityId"]["enum"]) == G0_CAPABILITY_IDS


def test_matrix_request_requires_frozen_category_order(tmp_path: Path):
    request = _request(tmp_path)
    validate_matrix_request(request)
    request["cases"][0], request["cases"][1] = (
        request["cases"][1],
        request["cases"][0],
    )
    with pytest.raises(G0MatrixError, match="six-category order"):
        validate_matrix_request(request)


def test_matrix_request_rejects_source_hash_drift(tmp_path: Path):
    request = _request(tmp_path)
    Path(request["cases"][0]["source"]["dimensioned_drawing"]["path"]).write_bytes(
        b"changed"
    )
    with pytest.raises(G0MatrixError, match="SHA-256 mismatch"):
        validate_matrix_request(request)


def test_matrix_request_rejects_publication_in_source_directory(tmp_path: Path):
    request = _request(tmp_path)
    source_path = Path(
        request["cases"][0]["source"]["dimensioned_drawing"]["path"]
    )
    request["cases"][0]["publication_directory"] = str(source_path.parent)
    with pytest.raises(G0MatrixError, match="aliases a source directory"):
        validate_matrix_request(request)


def test_matrix_summary_requires_exact_case_set(tmp_path: Path):
    request = _request(tmp_path)
    with pytest.raises(G0MatrixError, match="every requested case"):
        build_matrix_summary(request, {})


def test_matrix_summary_aggregates_covered_and_missing_rows(tmp_path: Path):
    request = _request(tmp_path)
    reports = {}
    for case in request["cases"]:
        evidence = _evidence(case, support_view_bounds=True)
        evidence_path = Path(case["publication_directory"]) / "layout-boundary-evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        reports[case["case_id"]] = {
            "evidence": evidence,
            "evidence_path": str(evidence_path),
            "evidence_file_sha256": file_sha256(evidence_path),
        }
    summary = build_matrix_summary(request, reports)
    coverage = {row["id"]: row for row in summary["coverage"]}
    assert summary["overall_status"] == "incomplete"
    assert coverage["view_outline_bounds"]["status"] == "covered"
    assert len(coverage["view_outline_bounds"]["supported_case_ids"]) == 6
    assert coverage["leader_bounds"]["status"] == "missing"
    assert not summary["blockers"]


def test_summary_rejects_evidence_file_hash_drift(tmp_path: Path):
    request = _request(tmp_path)
    reports = {}
    for case in request["cases"]:
        evidence = _evidence(case)
        evidence_path = Path(case["publication_directory"]) / "evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        reports[case["case_id"]] = {
            "evidence": evidence,
            "evidence_path": str(evidence_path),
            "evidence_file_sha256": "f" * 64,
        }
    with pytest.raises(G0MatrixError, match="file hash drift"):
        build_matrix_summary(request, reports)


def test_summary_rejects_evidence_object_file_divergence(tmp_path: Path):
    request = _request(tmp_path)
    reports = {}
    for case in request["cases"]:
        evidence = _evidence(case)
        persisted = copy.deepcopy(evidence)
        persisted["probe_id"] += "-persisted"
        evidence_path = Path(case["publication_directory"]) / "evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(persisted), encoding="utf-8")
        reports[case["case_id"]] = {
            "evidence": evidence,
            "evidence_path": str(evidence_path),
            "evidence_file_sha256": file_sha256(evidence_path),
        }
    with pytest.raises(G0MatrixError, match="differs from its persisted file"):
        build_matrix_summary(request, reports)


def test_builder_consumes_six_verified_f7_evidence_rows(tmp_path: Path):
    f7_root = tmp_path / "f7"
    for index, category in enumerate(G0_MATRIX_CATEGORIES):
        source = f7_root / "artifacts" / category
        plan = _artifact(source / "dimension_plan.json", b"{}")
        drawing = _artifact(source / "dimensioned.SLDDRW", bytes([index]))
        sidecar = _artifact(source / "dimension.verify.json", b"{}")
        evidence = {
            "protocol_id": "solidworks-dimension-f7-case-evidence",
            "case_id": "F7-" + category,
            "category": category,
            "plan": {"path": plan["path"], "file_sha256": plan["sha256"]},
            "output": {
                "path": drawing["path"],
                "sha256": drawing["sha256"],
                "verification_sidecar_path": sidecar["path"],
                "verification_sidecar_sha256": sidecar["sha256"],
            },
            "invariants": {
                "source_hashes_unchanged": True,
                "save_close_readonly_reopen": True,
                "independent_readonly_verify": True,
                "persisted_fingerprint_match": True,
            },
        }
        f7_root.mkdir(parents=True, exist_ok=True)
        (f7_root / f"{category}.evidence.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )
    request = build_matrix_request_from_f7(
        f7_root, tmp_path / "matrix", matrix_id="G0-SIX-CATEGORY"
    )
    assert tuple(row["category"] for row in request["cases"]) == G0_MATRIX_CATEGORIES
    validate_matrix_request(request)


def test_atomic_publication_refuses_overwrite(tmp_path: Path):
    target = tmp_path / "summary.json"
    publish_json_once(target, {"value": 1})
    with pytest.raises(G0MatrixError, match="refusing to overwrite"):
        publish_json_once(target, {"value": 2})
