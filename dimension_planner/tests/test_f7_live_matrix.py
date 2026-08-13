from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from drawing_planner.planning_models import canonical_json_sha256
from dimension_planner.f7_evidence import (
    F7_CATEGORIES,
    F7_DIMENSION_KINDS,
    F7_EXECUTION_ELEMENTS,
    DimensionF7EvidenceError,
    build_f7_capability_promotion_candidate,
    build_f7_case_evidence_from_semantic_results,
    build_f7_summary,
    publish_json_once,
    validate_f7_matrix_request,
)
from dimension_planner.tests.test_f2_dimension_plan import _plan


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "dimension_planner/contracts"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict:
    f0 = tmp_path / "dimension-f0-live-probe-summary.json"
    _write_json(
        f0,
        {
            "protocol_id": "solidworks-dimension-api-probe-run-summary",
            "schema_version": "1.0",
            "case_count": 1,
            "failed_count": 0,
            "matrix": {
                "overall_status": "complete",
                "research_coverage_complete": True,
                "production_frozen_case_count": 1,
            },
            "cases": [{"status": "evaluated"}],
        },
    )
    request = {
        "protocol_id": "solidworks-dimension-f7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "f0_evidence": {"path": str(f0.resolve()), "sha256": _sha(f0)},
        "cases": [],
    }
    chunks = [F7_DIMENSION_KINDS[index : index + 3] for index in range(0, 18, 3)]
    for index, (category, kinds) in enumerate(zip(F7_CATEGORIES, chunks, strict=True)):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        live_root = tmp_path / f"live-{index}"
        live_root.mkdir()
        plan = _plan(case_root, kinds)
        plan["plan_id"] = f"DP-F7-{index}"
        for key, payload in (
            ("handoff", {"handoff": index}),
            ("source_model", f"model-{index}".encode()),
            ("source_drawing", f"drawing-{index}".encode()),
            ("view_plan", {"view_plan": index}),
            ("verification_sidecar", {"verified": True, "case": index}),
        ):
            artifact = Path(plan[key]["path"])
            if isinstance(payload, bytes):
                artifact.write_bytes(payload)
            else:
                _write_json(artifact, payload)
            plan[key]["sha256"] = _sha(artifact)
        if index == 0:
            plan["dimensions"][0]["tolerance"] = {
                "kind": "bilateral",
                "lower_si": -0.0001,
                "upper_si": 0.0002,
                "fit_code": None,
            }
            plan["dimensions"][0]["display_format"]["prefix"] = "TYP "
        plan_path = case_root / "dimension_plan.json"
        _write_json(plan_path, plan)
        planning_request = {
            "schema_version": "1.0",
            "handoff_path": plan["handoff"]["path"],
            "handoff_sha256": plan["handoff"]["sha256"],
            "planner_profile": "production",
            "publication_directory": str(case_root.resolve()),
            "user_requirements": {},
        }
        request["cases"].append(
            {
                "case_id": f"F7-{index}",
                "category": category,
                "plan_path": str(plan_path.resolve()),
                "plan_file_sha256": _sha(plan_path),
                "plan_canonical_sha256": canonical_json_sha256(plan, "DimensionPlan"),
                "planning_request": planning_request,
                "planning_request_sha256": canonical_json_sha256(
                    planning_request, "dimension planning request"
                ),
                "output_path": str((live_root / "dimensioned.SLDDRW").resolve()),
                "evidence_path": str((live_root / "f7-case-evidence.json").resolve()),
            }
        )
    return request


def _verification_row(dimension: dict) -> dict:
    return {
        "dimension_id": dimension["dimension_id"],
        "view": "Q3DS_VP_front",
        "selection_name": f"Q3DS_DIM_{dimension['dimension_id']}",
        "full_name": "",
        "native_type": 1,
        "is_hole_callout": dimension["kind"].startswith("hole_"),
        "value_si": dimension["value"]["nominal_si"],
        "position_sheet_m": dimension["initial_position_sheet_m"],
        "prefix": dimension["display_format"]["prefix"],
        "suffix": dimension["display_format"]["suffix"],
        "text": dimension["display_format"]["prefix"] + "10",
        "precision": dimension["display_format"]["precision"],
        "driven_state": 1,
        "use_document_units": False,
        "unit": 0,
        "show_parentheses": dimension["display_format"]["show_parentheses"],
        "display_as_chain": False,
        "hole_callout_variables": [],
        "tolerance": None,
        "model_persistent_references": [
            attachment["model_persistent_reference"]
            for attachment in dimension["attachments"]
        ],
    }


def _materialize_evidence(request: dict) -> list[Path]:
    paths: list[Path] = []
    for case in request["cases"]:
        plan = json.loads(Path(case["plan_path"]).read_text(encoding="utf-8"))
        output = Path(case["output_path"])
        output.write_bytes(("dimensioned-" + case["case_id"]).encode())
        output_sha = _sha(output)
        rows = [_verification_row(dimension) for dimension in plan["dimensions"]]
        planned_ids = [dimension["dimension_id"] for dimension in plan["dimensions"]]
        handles = {
            dimension_id: f"Q3DS_DIM_{dimension_id}" for dimension_id in planned_ids
        }
        frozen = {
            "dimension_plan": case["plan_file_sha256"],
            "handoff": _sha(Path(plan["handoff"]["path"])),
            "source_model": _sha(Path(plan["source_model"]["path"])),
            "source_drawing": _sha(Path(plan["source_drawing"]["path"])),
            "view_plan": _sha(Path(plan["view_plan"]["path"])),
            "verification_sidecar": _sha(Path(plan["verification_sidecar"]["path"])),
        }
        verification = {
            "verified": True,
            "actual_total_count": len(rows),
            "baseline_count": 0,
            "planned_count": len(rows),
            "dimensions": rows,
        }
        sidecar_path = Path(str(output) + ".dimension-verification.json")
        sidecar = {
            "protocol_id": "solidworks-dimension-drawing-verification",
            "schema_version": "1.0",
            "operation_id": f"f7-{case['case_id']}",
            "generated_at_utc": "2026-08-13T08:00:00Z",
            "plan_id": plan["plan_id"],
            "plan_file_path": case["plan_path"],
            "plan_file_sha256": case["plan_file_sha256"],
            "plan_canonical_sha256": case["plan_canonical_sha256"],
            "output_path": str(output.resolve()),
            "artifact_sha256": output_sha,
            "verified": True,
            "dimension_handles": handles,
            "in_memory_verification": copy.deepcopy(verification),
            "reopen_verification": copy.deepcopy(verification),
            "frozen_inputs": frozen,
        }
        _write_json(sidecar_path, sidecar)
        stage = {
            "ok": True,
            "status": "COMPLETED",
            "planning_request_sha256": case["planning_request_sha256"],
            "plan_canonical_sha256": case["plan_canonical_sha256"],
        }
        evidence = {
            "protocol_id": "solidworks-dimension-f7-case-evidence",
            "schema_version": "1.0",
            "case_id": case["case_id"],
            "category": case["category"],
            "generated_at_utc": "2026-08-13T08:01:00Z",
            "solidworks_revision": "33.5.0",
            "execution_service_sha256": "a" * 64,
            "capability_manifest_sha256": "b" * 64,
            "plan": {
                "plan_id": plan["plan_id"],
                "path": case["plan_path"],
                "file_sha256": case["plan_file_sha256"],
                "canonical_sha256": case["plan_canonical_sha256"],
                "planning_request_sha256": case["planning_request_sha256"],
            },
            "output": {
                "path": str(output.resolve()),
                "sha256": output_sha,
                "verification_sidecar_path": str(sidecar_path.resolve()),
                "verification_sidecar_sha256": _sha(sidecar_path),
            },
            "frozen_inputs": frozen,
            "planned_dimension_ids": planned_ids,
            "planned_kinds": sorted(
                {dimension["kind"] for dimension in plan["dimensions"]}
            ),
            "verified_dimension_ids": planned_ids,
            "stages": {
                "validate": {**stage, "status": "VALID"},
                "create": copy.deepcopy(stage),
                "verify": copy.deepcopy(stage),
            },
            "invariants": {
                "no_dangling": True,
                "no_duplicate": True,
                "no_unplanned": True,
                "source_hashes_unchanged": True,
                "save_close_readonly_reopen": True,
                "independent_readonly_verify": True,
                "persisted_fingerprint_match": True,
            },
        }
        evidence_path = Path(case["evidence_path"])
        _write_json(evidence_path, evidence)
        paths.append(evidence_path)
    return paths


def test_f7_contracts_are_valid_draft_2020_12() -> None:
    for name in (
        "dimension-f7-matrix-request.schema.json",
        "dimension-f7-case-evidence.schema.json",
        "dimension-f7-summary.schema.json",
    ):
        Draft202012Validator.check_schema(
            json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
        )


def test_f7_request_requires_six_unique_categories_and_new_outputs(tmp_path: Path) -> None:
    request = _fixture(tmp_path)
    validated = validate_f7_matrix_request(request)
    assert {case["category"] for case in validated["cases"]} == set(F7_CATEGORIES)

    duplicate = copy.deepcopy(request)
    duplicate["cases"][0]["category"] = "threaded"
    with pytest.raises(DimensionF7EvidenceError, match="missing"):
        validate_f7_matrix_request(duplicate)

    Path(request["cases"][0]["output_path"]).write_bytes(b"existing")
    with pytest.raises(DimensionF7EvidenceError, match="must be new"):
        validate_f7_matrix_request(request)


def test_f7_request_rejects_incomplete_or_failed_f0_evidence(tmp_path: Path) -> None:
    request = _fixture(tmp_path)
    f0_path = Path(request["f0_evidence"]["path"])
    f0 = json.loads(f0_path.read_text(encoding="utf-8"))
    f0["matrix"]["overall_status"] = "incomplete"
    _write_json(f0_path, f0)
    request["f0_evidence"]["sha256"] = _sha(f0_path)
    with pytest.raises(DimensionF7EvidenceError, match="incomplete"):
        validate_f7_matrix_request(request)


def test_f7_summary_requires_exact_persisted_inventory_and_all_coverage(
    tmp_path: Path,
) -> None:
    request = _fixture(tmp_path)
    validate_f7_matrix_request(request)
    evidence_paths = _materialize_evidence(request)
    summary = build_f7_summary(request, evidence_paths)
    assert summary["overall_status"] == "complete"
    assert all(summary["category_coverage"][name] == 1 for name in F7_CATEGORIES)
    assert all(summary["dimension_kind_coverage"][name] == 1 for name in F7_DIMENSION_KINDS)
    assert all(summary["element_coverage"][name] > 0 for name in F7_EXECUTION_ELEMENTS)

    tampered = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    tampered["verified_dimension_ids"] = tampered["verified_dimension_ids"][:-1]
    _write_json(evidence_paths[0], tampered)
    with pytest.raises(DimensionF7EvidenceError):
        build_f7_summary(request, evidence_paths)


def test_only_complete_f7_summary_can_generate_a_valid_promotion_candidate(
    tmp_path: Path,
) -> None:
    request = _fixture(tmp_path)
    evidence_paths = _materialize_evidence(request)
    summary = build_f7_summary(request, evidence_paths)
    summary_path = tmp_path / "dimension-f7-live-matrix-summary.json"
    publish_json_once(summary, summary_path)
    current = json.loads(
        (ROOT / "dimension_planner/capabilities/current.json").read_text(encoding="utf-8")
    )
    promoted = build_f7_capability_promotion_candidate(current, summary_path)
    evidence_sha = _sha(summary_path)
    assert promoted["registry_version"] == "0.4.0"
    assert promoted["executor_version"] == "0.3.0"
    assert promoted["live_evidence"]["summary_sha256"] == evidence_sha
    assert all(
        promoted["dimension_types"][name]["status"] == "supported"
        for name in F7_DIMENSION_KINDS
    )
    assert all(
        promoted["elements"][name]["status"] == "supported"
        for name in F7_EXECUTION_ELEMENTS
    )
    assert promoted["elements"]["annotation_text_bounds"]["status"] == "unsupported"

    incomplete = copy.deepcopy(summary)
    incomplete["overall_status"] = "incomplete"
    incomplete_path = tmp_path / "incomplete-summary.json"
    publish_json_once(incomplete, incomplete_path)
    with pytest.raises(DimensionF7EvidenceError, match="incomplete"):
        build_f7_capability_promotion_candidate(current, incomplete_path)


def test_case_evidence_requires_public_stage_continuity_and_independent_reopen(
    tmp_path: Path,
) -> None:
    request = _fixture(tmp_path)
    validate_f7_matrix_request(request)
    _materialize_evidence(request)
    case = request["cases"][0]
    executable = tmp_path / "SolidworksExecution.exe"
    executable.write_bytes(b"f7-executor")
    manifest = ROOT / "dimension_planner/capabilities/current.json"
    common = {
        "ok": True,
        "planning_request_sha256": case["planning_request_sha256"],
        "plan_canonical_sha256": case["plan_canonical_sha256"],
    }
    stages = {
        "validate": {**common, "status": "VALID"},
        "create": {**common, "status": "COMPLETED"},
        "verify": {
            **common,
            "status": "COMPLETED",
            "executor": {"independent_read_only_reopen": True},
        },
    }
    evidence = build_f7_case_evidence_from_semantic_results(
        case,
        stages,
        execution_service_path=executable,
        capability_manifest_path=manifest,
    )
    assert evidence["invariants"] == {
        "no_dangling": True,
        "no_duplicate": True,
        "no_unplanned": True,
        "source_hashes_unchanged": True,
        "save_close_readonly_reopen": True,
        "independent_readonly_verify": True,
        "persisted_fingerprint_match": True,
    }

    stages["verify"].pop("executor")
    with pytest.raises(DimensionF7EvidenceError, match="independent read-only reopen"):
        build_f7_case_evidence_from_semantic_results(
            case,
            stages,
            execution_service_path=executable,
            capability_manifest_path=manifest,
        )


def test_f7_runner_uses_only_default_semantic_mcp() -> None:
    runner = (ROOT / "scripts/run_dimension_f7_live_matrix.py").read_text(
        encoding="utf-8"
    )
    assert "stdio_client" in runner
    assert "validate_part_drawing_dimension_plan" in runner
    assert "qualify_dimensioned_part_drawing" in runner
    assert "verify_qualified_dimensioned_part_drawing" in runner
    assert '!= "capability_blocked"' in runner
    assert 'status.get("com_attached") is not True' not in runner
    assert "managed semantic transaction owns its SolidWorks session" in runner
    assert "httpx" not in runner
    assert "/api/" not in runner

    semantic_server = (ROOT / "adapters/claude/server.py").read_text(
        encoding="utf-8"
    )
    binding = semantic_server[
        semantic_server.index("def _dimension_f7_case_binding(") :
        semantic_server.index("def _dimension_semantic_response_with_binding(")
    ]
    assert "matrix = validate_f7_matrix_request_for_evaluation(raw)" in binding
    assert "if require_existing_output" not in binding
