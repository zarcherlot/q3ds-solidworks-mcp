from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dimension_planner.f0_evidence import (
    CAPABILITY_PATH,
    CONTRACT_PATH,
    F0_CAPABILITY_IDS,
    F0CapabilityEvidenceError,
    evaluate_f0_evidence,
    load_f0_capability_manifest,
)


PROBE_CONTRACT_PATH = CONTRACT_PATH.with_name("dimension-api-probe.schema.json")


def _evidence(*, execution_mode: str = "offline") -> dict:
    manifest = load_f0_capability_manifest()
    digest = "a" * 64
    checks = {
        "native_api_invoked": False,
        "in_memory_readback": False,
        "save_close_readonly_reopen": False,
        "stable_identity": False,
        "attachment_readback": False,
        "position_readback": False,
        "text_bounds_readback": False,
    }
    return {
        "protocol_id": "solidworks-dimension-api-evidence",
        "schema_version": "1.0",
        "probe_id": "DPE-contract-fixture",
        "created_at": "2026-08-12T00:00:00Z",
        "execution_mode": execution_mode,
        "source_kind": "frozen_viewplan_drawing",
        "solidworks": {
            "major_version": 2025,
            "service_pack": "SP5",
            "revision": "33.5.0",
        },
        "source_request_sha256": digest,
        "upstream_immutability": [
            {
                "role": role,
                "path": f"D:\\evidence\\{role}.json",
                "sha256_before": digest,
                "sha256_after": digest,
            }
            for role in ("view_plan", "verified_drawing", "verification_sidecar")
        ],
        "capabilities": [
            {
                "id": item["id"],
                "status": "planned",
                "checks": copy.deepcopy(checks),
                "evidence": [],
                "limitations": ["live native API evidence not run"],
            }
            for item in manifest["capabilities"]
        ],
    }


def test_f0_contract_is_valid_draft_2020_12_schema():
    for path in (PROBE_CONTRACT_PATH, CONTRACT_PATH):
        contract = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(contract)


def test_f0_contracts_share_the_frozen_capability_catalog():
    probe = json.loads(PROBE_CONTRACT_PATH.read_text(encoding="utf-8"))
    evidence = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert tuple(probe["$defs"]["capabilityId"]["enum"]) == F0_CAPABILITY_IDS
    assert tuple(evidence["$defs"]["capabilityId"]["enum"]) == F0_CAPABILITY_IDS


def test_initial_manifest_is_fail_closed_and_has_frozen_order():
    manifest = load_f0_capability_manifest()
    assert manifest["registry_version"] == "0.1.0"
    assert len(manifest["capabilities"]) == 14
    assert tuple(item["id"] for item in manifest["capabilities"]) == F0_CAPABILITY_IDS
    assert all(item["status"] == "planned" for item in manifest["capabilities"])
    assert manifest["live_evidence"] is None


def test_offline_evidence_stays_incomplete():
    result = evaluate_f0_evidence(_evidence())
    assert result.overall_status == "incomplete"
    assert not result.blockers
    assert set(result.capability_statuses.values()) == {"planned"}


def test_offline_unsupported_evidence_cannot_complete_f0():
    evidence = _evidence()
    for row in evidence["capabilities"]:
        row["status"] = "unsupported"
        row["limitations"] = ["placeholder limitation"]
    result = evaluate_f0_evidence(evidence)
    assert result.overall_status == "capability_blocked"
    assert set(result.capability_statuses.values()) == {"planned"}
    assert len(result.blockers) == len(F0_CAPABILITY_IDS)


def test_live_unsupported_requires_native_evidence_and_target_revision():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][0]
    row["status"] = "unsupported"
    row["checks"]["native_api_invoked"] = True
    row["evidence"] = ["AddChamferDim returned null for a stable selection"]
    row["limitations"] = ["native API cannot create this form from the selected entities"]
    result = evaluate_f0_evidence(evidence)
    assert result.capability_statuses[row["id"]] == "unsupported"
    assert result.overall_status == "incomplete"

    evidence["solidworks"]["service_pack"] = "SP4"
    evidence["solidworks"]["revision"] = "33.4.0"
    result = evaluate_f0_evidence(evidence)
    assert result.capability_statuses[row["id"]] == "planned"
    assert result.overall_status == "capability_blocked"


def test_supported_requires_every_live_persistence_check():
    evidence = _evidence(execution_mode="live")
    evidence["capabilities"][0]["status"] = "supported"
    result = evaluate_f0_evidence(evidence)
    assert result.overall_status == "capability_blocked"
    assert result.capability_statuses["model_dimension_import"] == "planned"
    assert "complete persistence readback" in result.blockers[0]


def test_source_request_hash_must_bind_supplied_request():
    evidence = _evidence()
    source_request = {"protocol_id": "solidworks-dimension-api-probe"}
    with pytest.raises(F0CapabilityEvidenceError, match="does not bind"):
        evaluate_f0_evidence(evidence, source_request=source_request)


def test_complete_live_evidence_can_promote_one_capability():
    evidence = _evidence(execution_mode="live")
    row = evidence["capabilities"][0]
    row["status"] = "supported"
    row["checks"] = {key: True for key in row["checks"]}
    row["limitations"] = []
    row["evidence"] = ["native import and readback fingerprint matched"]
    result = evaluate_f0_evidence(evidence)
    assert not result.blockers
    assert result.capability_statuses["model_dimension_import"] == "supported"
    assert result.overall_status == "incomplete"


def test_upstream_hash_drift_blocks_all_live_conclusions():
    evidence = _evidence(execution_mode="live")
    evidence["upstream_immutability"][1]["sha256_after"] = "b" * 64
    result = evaluate_f0_evidence(evidence)
    assert result.overall_status == "capability_blocked"
    assert "upstream artifacts changed" in result.blockers[-1]


def test_upstream_roles_are_complete_ordered_and_unique():
    evidence = _evidence()
    evidence["upstream_immutability"][0]["role"] = "source_model"
    with pytest.raises(F0CapabilityEvidenceError, match="immutable artifacts"):
        evaluate_f0_evidence(evidence)


def test_research_pair_is_a_valid_f0_evidence_source():
    evidence = _evidence()
    evidence["source_kind"] = "research_model_drawing_pair"
    evidence["upstream_immutability"] = [
        {
            "role": role,
            "path": f"D:\\corpus\\{role}",
            "sha256_before": "a" * 64,
            "sha256_after": "a" * 64,
        }
        for role in ("source_model", "source_drawing", "drawing_template")
    ]
    result = evaluate_f0_evidence(evidence)
    assert result.overall_status == "incomplete"


def test_capability_catalog_must_be_complete_and_ordered():
    evidence = _evidence()
    evidence["capabilities"][0], evidence["capabilities"][1] = (
        evidence["capabilities"][1],
        evidence["capabilities"][0],
    )
    with pytest.raises(F0CapabilityEvidenceError, match="manifest order"):
        evaluate_f0_evidence(evidence)


def test_supported_manifest_requires_bound_live_evidence(tmp_path: Path):
    manifest = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    manifest["capabilities"][0]["status"] = "supported"
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(F0CapabilityEvidenceError, match="live_evidence"):
        load_f0_capability_manifest(path)


def test_manifest_catalog_cannot_be_redefined_with_evidence(tmp_path: Path):
    manifest = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    manifest["capabilities"][0]["id"] = "invented_capability"
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(F0CapabilityEvidenceError, match="frozen F0 catalog"):
        load_f0_capability_manifest(path)
