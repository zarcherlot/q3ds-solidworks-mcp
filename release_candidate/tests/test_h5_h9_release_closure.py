from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from release_candidate import h5_h9_release_closure as closure
from release_candidate.h2_session_preflight import PRODUCTION_SCHEDULE
from release_candidate.tests.test_h1_chain_evidence import COMMIT, _fixture


def _write(path: Path, value: object) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {"path": str(path.resolve()), "sha256": _sha(path)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request_fixture(tmp_path: Path) -> tuple[dict, dict, dict]:
    h1 = _fixture(tmp_path)
    h1_binding = _write(tmp_path / "h1-chain-evidence.json", h1)
    manifest = {
        "planned_outputs": {"h1_candidate": h1_binding["path"]},
        "schedule": [
            {
                "sequence": index,
                "stage_order": stage,
                "skill": skill,
                "tool": tool,
                "mutating": mutating,
            }
            for index, (stage, skill, tool, mutating) in enumerate(
                PRODUCTION_SCHEDULE, 1
            )
        ],
    }
    manifest_binding = _write(tmp_path / "session-manifest.json", manifest)
    request = {
        "protocol_id": "solidworks-five-skill-release-closure-request",
        "schema_version": "1.0",
        "h3_session_manifest": manifest_binding,
        "h1_chain_evidence": h1_binding,
    }
    return request, h1, manifest


def _fake_inventory(tmp_path: Path) -> list[dict]:
    required = {
        "skill": 5,
        "plan_schema": 3,
        "plan": 3,
        "capability_manifest": 4,
        "execution_runtime": 1,
        "source_input": 2,
        "drawing": 4,
        "verification_sidecar": 3,
        "semantic_response": 16,
        "semantic_call_claim": 16,
    }
    rows = []
    counter = 0
    for category, count in required.items():
        for index in range(1, count + 1):
            counter += 1
            path = tmp_path / "freeze" / f"artifact-{counter:02d}.bin"
            binding = _write(path, f"{category}-{index}".encode())
            rows.append(
                {
                    "category": category,
                    "role": f"{category}.{index:02d}",
                    "path": binding["path"],
                    "size_bytes": path.stat().st_size,
                    "sha256": binding["sha256"],
                }
            )
    return rows


def test_h5_h9_contracts_are_valid_draft_2020_12() -> None:
    for path in (closure.REQUEST_SCHEMA_PATH, closure.CANDIDATE_SCHEMA_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_h5_h9_loads_only_hash_bound_request(tmp_path: Path) -> None:
    request, _, _ = _request_fixture(tmp_path)
    path = tmp_path / "release-request.json"
    _write(path, request)
    assert closure.load_h5_h9_release_request(path, _sha(path))["schema_version"] == "1.0"
    with pytest.raises(closure.H5H9ReleaseClosureError, match="SHA-256 mismatch"):
        closure.load_h5_h9_release_request(path, "f" * 64)


def test_h5_requires_h3_finalized_h1_traceability(tmp_path: Path, monkeypatch) -> None:
    request, h1, manifest = _request_fixture(tmp_path)
    monkeypatch.setattr(
        closure,
        "inspect_h3_session",
        lambda *_: {
            "status": "ready_to_finalize",
            "captured_operation_count": 16,
        },
    )
    result = closure._gate_h5_traceability(request, h1, manifest)
    assert result["id"] == "H5"
    assert result["status"] == "passed"

    manifest["planned_outputs"]["h1_candidate"] = str(tmp_path / "other.json")
    with pytest.raises(closure.H5H9ReleaseClosureError, match="H3-finalized"):
        closure._gate_h5_traceability(request, h1, manifest)


def test_h6_independently_rejects_schema_invalid_plan(tmp_path: Path) -> None:
    _, h1, _ = _request_fixture(tmp_path)
    with pytest.raises(closure.H5H9ReleaseClosureError, match="violates.*Schema"):
        closure._gate_h6_plan_contracts(
            Path(__file__).resolve().parents[2], h1, []
        )


def test_h6_csharp_contract_hashes_bind_exact_plan_schemas() -> None:
    root = Path(__file__).resolve().parents[2]
    for role, (relative, protocol, version) in closure._PLAN_ROWS.items():
        family = "layout" if role == "layout_plan" else role.removesuffix("_plan")
        closure._validate_csharp_contract_binding(
            root / closure._CSHARP_CONTRACT_ROWS[family][0],
            root / relative,
            protocol,
            version,
            family,
        )


def test_h7_requires_all_sixteen_hash_bound_stdio_claims(tmp_path: Path) -> None:
    request, h1, manifest = _request_fixture(tmp_path)
    root = Path(__file__).resolve().parents[2]
    contract = json.loads(
        (root / "adapters/claude/contracts/skill-chain.contract.json").read_text(
            encoding="utf-8"
        )
    )
    h0_path = Path(h1["h0_readiness"]["path"])
    h0 = json.loads(h0_path.read_text(encoding="utf-8"))
    h0["semantic_mcp"]["tools"] = contract["default_mcp"]["tools"]
    for role, relative in {
        "contract": "adapters/claude/contracts/skill-chain.contract.json",
        "config": ".codex/config.toml",
        "schema": "adapters/claude/contracts/semantic-tools.schema.json",
    }.items():
        path = (root / relative).resolve(strict=True)
        h0["semantic_mcp"][role] = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha(path),
        }
    h0["skills"] = []
    for stage in contract["stages"]:
        path = (root / stage["path"]).resolve(strict=True)
        h0["skills"].append(
            {
                "name": stage["skill"],
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    _write(h0_path, h0)
    h1["h0_readiness"]["sha256"] = _sha(h0_path)
    response_directory = tmp_path / "responses"
    claim_directory = response_directory / ".h4-claims"
    claim_directory.mkdir(parents=True)
    manifest["planned_outputs"]["response_directory"] = str(response_directory)
    session_sha = request["h3_session_manifest"]["sha256"]
    for row in manifest["schedule"]:
        _write(
            claim_directory / f"{row['sequence']:02d}-{row['tool']}.json",
            {
                "protocol_id": "solidworks-five-skill-semantic-call-claim",
                "schema_version": "1.0",
                "session_manifest_sha256": session_sha,
                "sequence": row["sequence"],
                "tool": row["tool"],
                "arguments": {"sequence": row["sequence"]},
                "arguments_sha256": hashlib.sha256(
                    json.dumps(
                        {"sequence": row["sequence"]},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "broker_sha256": _sha(root / "release_candidate/h4_semantic_step.py"),
                "server_entry_sha256": _sha(root / "adapters/codex/server.py"),
                "semantic_contract_sha256": _sha(
                    root / "adapters/claude/contracts/skill-chain.contract.json"
                ),
                "execution_service_sha256": h1["execution_service"]["sha256"],
            },
        )
    extras: list[dict] = []
    result = closure._gate_h7_semantic_boundary(
        root, request, h1, manifest, extras
    )
    assert result["id"] == "H7"
    assert sum(row["category"] == "semantic_call_claim" for row in extras) == 16

    next(claim_directory.glob("*.json")).unlink()
    with pytest.raises(closure.H5H9ReleaseClosureError, match="sixteen"):
        closure._gate_h7_semantic_boundary(
            root, request, h1, manifest, []
        )


def test_h8_rejects_sidecar_without_save_reopen_proof(tmp_path: Path) -> None:
    _, h1, _ = _request_fixture(tmp_path)
    with pytest.raises(closure.H5H9ReleaseClosureError, match="ViewPlan transaction"):
        closure._gate_h8_transaction_integrity(
            Path(__file__).resolve().parents[2], h1
        )


def test_h8_accepts_three_independent_reopen_and_frozen_ledgers(
    tmp_path: Path, monkeypatch
) -> None:
    _, h1, _ = _request_fixture(tmp_path)
    outputs = {
        row["role"]: row
        for stage in h1["stages"]
        for row in stage["outputs"]
    }
    immutable = {
        row["role"]: row["sha256_after"] for row in h1["immutable_inputs"]
    }
    _write(
        Path(outputs["view_verification_sidecar"]["path"]),
        {
            "schema_version": "1.0",
            "verified": True,
            "output_path": outputs["view_drawing"]["path"],
            "artifact_sha256": outputs["view_drawing"]["sha256"],
            "verification": {"views": []},
        },
    )
    dimension = {
        "output_path": outputs["dimensioned_drawing"]["path"],
        "artifact_sha256": outputs["dimensioned_drawing"]["sha256"],
        "in_memory_verification": {"verified": True},
        "reopen_verification": {"verified": True},
        "frozen_inputs": {
            "dimension_plan": outputs["dimension_plan"]["sha256"],
            "handoff": outputs["dimension_handoff"]["sha256"],
            "source_model": immutable["source_model"],
            "source_drawing": outputs["view_drawing"]["sha256"],
            "view_plan": outputs["view_plan"]["sha256"],
            "verification_sidecar": outputs["view_verification_sidecar"]["sha256"],
        },
    }
    verification = {
        "verified": True,
        "layout_fingerprint_sha256": "a" * 64,
    }
    layout = {
        "output_path": outputs["final_drawing"]["path"],
        "artifact_sha256": outputs["final_drawing"]["sha256"],
        "in_memory_verification": dict(verification),
        "reopen_verification": dict(verification),
        "frozen_inputs": {
            "drawing_layout_plan": outputs["layout_plan"]["sha256"],
            "handoff": outputs["layout_handoff"]["sha256"],
            "dimension_plan": outputs["dimension_plan"]["sha256"],
            "source_drawing": outputs["dimensioned_drawing"]["sha256"],
            "dimension_verification_sidecar": outputs[
                "dimension_verification_sidecar"
            ]["sha256"],
        },
    }

    def sidecar(_path: Path, _schema: Path, label: str) -> dict:
        return dimension if label.startswith("dimension") else layout

    monkeypatch.setattr(closure, "_validate_json_document", sidecar)
    for stage in h1["stages"]:
        for operation in stage["operations"]:
            if operation["tool"] in {
                "verify_part_drawing_view_plan",
                "verify_dimensioned_part_drawing",
                "verify_final_part_drawing",
            }:
                _write(
                    Path(operation["response"]["path"]),
                    {
                        "ok": True,
                        "status": "COMPLETED",
                        "executor": {"independent_read_only_reopen": True},
                    },
                )
    result = closure._gate_h8_transaction_integrity(
        Path(__file__).resolve().parents[2], h1
    )
    assert result["id"] == "H8"


def test_h9_requires_exact_clean_commit(tmp_path: Path, monkeypatch) -> None:
    inventory = _fake_inventory(tmp_path)
    monkeypatch.setattr(
        closure,
        "_git_state",
        lambda _: {"commit": COMMIT, "clean": True, "changed_paths": []},
    )
    assert closure._gate_h9_freeze(
        Path(__file__).resolve().parents[2], {"git_commit": COMMIT}, inventory
    )["status"] == "passed"
    monkeypatch.setattr(
        closure,
        "_git_state",
        lambda _: {"commit": COMMIT, "clean": False, "changed_paths": ["x"]},
    )
    with pytest.raises(closure.H5H9ReleaseClosureError, match="clean Git"):
        closure._gate_h9_freeze(
            Path(__file__).resolve().parents[2], {"git_commit": COMMIT}, inventory
        )


def test_h5_h9_builds_publishes_and_revalidates_complete_freeze(
    tmp_path: Path, monkeypatch
) -> None:
    request, _, _ = _request_fixture(tmp_path)
    inventory = _fake_inventory(tmp_path)

    monkeypatch.setattr(
        closure,
        "_gate_h5_traceability",
        lambda *_: closure._passed("H5", "traceability", "check one", "check two"),
    )
    monkeypatch.setattr(
        closure,
        "_gate_h6_plan_contracts",
        lambda *_: closure._passed("H6", "plan contracts", "check one", "check two"),
    )
    monkeypatch.setattr(
        closure,
        "_gate_h7_semantic_boundary",
        lambda *_: closure._passed("H7", "semantic boundary", "check one", "check two"),
    )
    monkeypatch.setattr(
        closure,
        "_gate_h8_transaction_integrity",
        lambda *_: closure._passed("H8", "transaction integrity", "check one", "check two"),
    )
    monkeypatch.setattr(
        closure,
        "_build_frozen_inventory",
        lambda *_: inventory,
    )
    monkeypatch.setattr(
        closure,
        "_gate_h9_freeze",
        lambda *_: closure._passed("H9", "final freeze", "check one", "check two"),
    )

    report = closure.build_h5_h9_release_candidate(
        request,
        Path(__file__).resolve().parents[2],
        generated_at_utc="2026-08-16T12:00:00Z",
    )
    assert report["status"] == "complete"
    assert [row["id"] for row in report["gates"]] == ["H5", "H6", "H7", "H8", "H9"]
    assert len(report["frozen_artifacts"]) == 57

    output = tmp_path / "published" / "release-candidate.json"
    output.parent.mkdir()
    result = closure.build_and_publish_h5_h9_release_candidate(
        request,
        Path(__file__).resolve().parents[2],
        output,
        generated_at_utc="2026-08-16T12:00:00Z",
    )
    assert result["status"] == "complete"
    assert output.is_file()

    Path(inventory[0]["path"]).write_bytes(b"drift")
    with pytest.raises(closure.H5H9ReleaseClosureError, match="drifted"):
        closure.validate_h5_h9_release_candidate(report)


def test_h5_h9_rejects_h1_binding_drift(tmp_path: Path) -> None:
    request, _, _ = _request_fixture(tmp_path)
    Path(request["h1_chain_evidence"]["path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(closure.H5H9ReleaseClosureError, match="binding"):
        closure.build_h5_h9_release_candidate(
            request, Path(__file__).resolve().parents[2]
        )
