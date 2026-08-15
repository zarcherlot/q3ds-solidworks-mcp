from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from drawing_layout_planner.g0_evidence import (
    CAPABILITY_PATH,
    CONTRACT_PATH,
    G0_CAPABILITY_IDS,
    G0BoundaryEvidenceError,
    evaluate_g0_evidence,
    load_g0_capability_manifest,
)


PROBE_PATH = CONTRACT_PATH.with_name("layout-boundary-probe.schema.json")


def _canonical(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence(
    *, execution_mode: str = "offline", source_kind: str = "verified_dimension_drawing"
) -> dict:
    digest = "a" * 64
    checks = {
        "native_api_invoked": False,
        "objects_observed": False,
        "bounds_structured": False,
        "rebuild_compared": False,
        "save_reopen_compared": False,
        "within_error_budget": False,
    }
    return {
        "protocol_id": "solidworks-layout-boundary-evidence",
        "schema_version": "1.0",
        "probe_id": "LBE-contract-fixture",
        "created_at": "2026-08-14T00:00:00Z",
        "execution_mode": execution_mode,
        "source_kind": source_kind,
        "solidworks": {
            "major_version": 2025,
            "service_pack": "SP5",
            "revision": "33.5.0",
        },
        "source_request_sha256": digest,
        "error_budget_m": 0.0005,
        "upstream_immutability": [
            {
                "role": role,
                "path": f"D:\\g0\\{role}",
                "sha256_before": digest,
                "sha256_after": digest,
            }
            for role in {
                "verified_dimension_drawing": (
                    "dimension_plan", "dimensioned_drawing", "dimension_verification_sidecar"
                ),
                "verified_view_plan_drawing": (
                    "view_plan", "view_drawing", "view_verification_sidecar"
                ),
                "verified_layout_fixture": (
                    "layout_fixture_manifest", "fixture_drawing", "source_verification_sidecar"
                ),
            }[source_kind]
        ],
        "snapshots": {
            "before_rebuild_sha256": digest,
            "after_rebuild_sha256": digest,
            "readonly_reopen_sha256": digest,
        },
        "capabilities": [
            {
                "id": capability_id,
                "status": "planned",
                "checks": copy.deepcopy(checks),
                "max_drift_m": None,
                "evidence": [],
                "limitations": ["live G0 probe not run"],
            }
            for capability_id in G0_CAPABILITY_IDS
        ],
    }


def test_g0_contracts_are_valid_draft_2020_12_schemas():
    for path in (PROBE_PATH, CONTRACT_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_contracts_and_manifest_share_frozen_catalog():
    request = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
    evidence = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    manifest = load_g0_capability_manifest()
    assert tuple(request["$defs"]["capabilityId"]["enum"]) == G0_CAPABILITY_IDS
    assert tuple(evidence["$defs"]["capabilityId"]["enum"]) == G0_CAPABILITY_IDS
    assert tuple(row["id"] for row in manifest["capabilities"]) == G0_CAPABILITY_IDS
    assert {row["status"] for row in manifest["capabilities"]} == {"supported"}
    assert manifest["verification"] == "live_complete"


def test_offline_evidence_remains_incomplete():
    result = evaluate_g0_evidence(_evidence())
    assert result.overall_status == "incomplete"
    assert not result.blockers
    assert set(result.capability_statuses.values()) == {"planned"}


def test_supported_requires_all_live_boundary_and_drift_checks():
    evidence = _evidence(execution_mode="live")
    evidence["capabilities"][0]["status"] = "supported"
    result = evaluate_g0_evidence(evidence)
    assert result.overall_status == "capability_blocked"
    assert result.capability_statuses["view_outline_bounds"] == "planned"
    assert "stable rebuild/reopen" in result.blockers[0]


def test_one_observed_capability_can_be_supported_without_promoting_others():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][0]
    row["status"] = "supported"
    row["checks"] = {name: True for name in row["checks"]}
    row["max_drift_m"] = 0.0001
    row["evidence"] = ["four model views matched across rebuild and reopen"]
    row["limitations"] = []
    result = evaluate_g0_evidence(evidence)
    assert not result.blockers
    assert result.overall_status == "incomplete"
    assert result.capability_statuses["view_outline_bounds"] == "supported"


def test_error_budget_is_enforced_beyond_boolean_claim():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][0]
    row["status"] = "supported"
    row["checks"] = {name: True for name in row["checks"]}
    row["max_drift_m"] = 0.001
    row["evidence"] = ["drift measured"]
    result = evaluate_g0_evidence(evidence)
    assert result.capability_statuses[row["id"]] == "planned"
    assert result.overall_status == "capability_blocked"


def test_missing_object_class_must_stay_planned_not_unsupported():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][5]
    row["status"] = "unsupported"
    result = evaluate_g0_evidence(evidence)
    assert result.capability_statuses["section_symbol_bounds"] == "planned"
    assert result.overall_status == "capability_blocked"


def test_live_unsupported_requires_native_limitation_evidence():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][2]
    row["status"] = "unsupported"
    row["checks"]["native_api_invoked"] = True
    row["evidence"] = ["GetDisplayData returned text anchors only"]
    row["limitations"] = ["exact glyph width is not exposed"]
    result = evaluate_g0_evidence(evidence)
    assert not result.blockers
    assert result.capability_statuses["note_text_bounds"] == "unsupported"


def test_upstream_drift_blocks_live_conclusions():
    evidence = _evidence(execution_mode="live")
    evidence["upstream_immutability"][1]["sha256_after"] = "b" * 64
    result = evaluate_g0_evidence(evidence)
    assert result.overall_status == "capability_blocked"
    assert "artifacts changed" in result.blockers[-1]


def test_upstream_order_is_fixed():
    evidence = _evidence()
    evidence["upstream_immutability"].reverse()
    with pytest.raises(G0BoundaryEvidenceError, match="artifact order"):
        evaluate_g0_evidence(evidence)


def test_verified_view_plan_source_has_its_own_frozen_upstream_order():
    evidence = _evidence(source_kind="verified_view_plan_drawing")
    assert evaluate_g0_evidence(evidence).overall_status == "incomplete"
    evidence["upstream_immutability"][0]["role"] = "dimension_plan"
    with pytest.raises(G0BoundaryEvidenceError, match="artifact order"):
        evaluate_g0_evidence(evidence)


def test_verified_layout_fixture_source_has_its_own_frozen_upstream_order():
    evidence = _evidence(source_kind="verified_layout_fixture")
    assert evaluate_g0_evidence(evidence).overall_status == "incomplete"


def test_request_hash_must_bind_original_candidate():
    request = {"protocol_id": "solidworks-layout-boundary-probe"}
    evidence = _evidence()
    evidence["source_request_sha256"] = _canonical(request)
    assert evaluate_g0_evidence(evidence, source_request=request).overall_status == "incomplete"
    evidence["source_request_sha256"] = "b" * 64
    with pytest.raises(G0BoundaryEvidenceError, match="does not bind"):
        evaluate_g0_evidence(evidence, source_request=request)


def test_manifest_catalog_and_live_evidence_are_fail_closed(tmp_path: Path):
    manifest = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    manifest["capabilities"][0]["id"] = "invented"
    path = tmp_path / "bad-catalog.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(G0BoundaryEvidenceError, match="frozen G0 catalog"):
        load_g0_capability_manifest(path)

    manifest = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    manifest["capabilities"][0]["status"] = "supported"
    manifest["live_evidence"] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(G0BoundaryEvidenceError, match="live_evidence"):
        load_g0_capability_manifest(path)
