from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from drawing_planner.planning_models import canonical_json_sha256
from drawing_layout_planner.capability_registry import (
    DrawingLayoutExecutionCapabilityError,
    current_registry,
)
from drawing_layout_planner.g7_evidence import (
    G7_NEGATIVE_SCENARIOS,
    G7_POSITIVE_SCENARIOS,
    DrawingLayoutG7EvidenceError,
    _validate_scenario_binding,
    build_g7_capability_promotion_candidate,
    build_g7_negative_case_evidence,
    load_g7_contracts,
    validate_g7_matrix_request,
)
from drawing_layout_planner.g0_evidence import G0_CAPABILITY_IDS
from drawing_layout_planner.planner_engine import DrawingLayoutPlannerEngine
from drawing_layout_planner.tests.test_g3_layout_engine import _fixture


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_g7_contract_schemas_are_valid_draft_2020_12() -> None:
    for schema in load_g7_contracts():
        Draft202012Validator.check_schema(schema)


def test_current_g0_boundary_and_g7_registry_are_qualification_eligible(
    tmp_path: Path,
) -> None:
    request, _, supported_fixture_registry = _fixture(tmp_path)
    result = DrawingLayoutPlannerEngine(
        capabilities=supported_fixture_registry
    ).plan(request)
    assert result.plan is not None
    plan = json.loads(Path(result.plan.path).read_text(encoding="utf-8"))

    current_registry().require_qualification_eligible(plan)


def test_g7_matrix_binds_exact_scenarios_requests_and_distinct_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "drawing_layout_planner.g7_evidence._validate_scenario_binding",
        lambda scenario, plan, index: None,
    )
    qualification = tmp_path / "g0-qualification.json"
    _write_json(
        qualification,
        {
            "protocol_id": "solidworks-layout-g0-qualification",
            "schema_version": "1.0",
            "qualification_id": "G0-G7-TEST",
            "created_at": "2026-08-15T00:00:00Z",
            "overall_status": "complete",
            "solidworks_revision": "33.5.0",
            "base_matrix": {"path": "C:\\fixture\\matrix.json", "sha256": "a" * 64},
            "supplemental_evidence": [
                {"path": f"C:\\fixture\\supplemental-{index}.json", "sha256": chr(98 + index) * 64}
                for index in range(3)
            ],
            "capabilities": [
                {
                    "id": item,
                    "status": "supported",
                    "evidence_paths": ["C:\\fixture\\evidence.json"],
                    "max_drift_m": 0.0,
                    "limitation": None,
                }
                for item in G0_CAPABILITY_IDS
            ],
        },
    )
    boundary_manifest = tmp_path / "g0-current.json"
    _write_json(
        boundary_manifest,
        {
            "live_evidence": {
                "qualification_path": str(qualification.resolve()),
                "qualification_sha256": _sha(qualification),
                "qualification_id": "G0-G7-TEST",
            }
        },
    )
    monkeypatch.setattr(
        "drawing_layout_planner.g7_evidence.BOUNDARY_CAPABILITY_PATH",
        boundary_manifest,
    )
    positive_cases = []
    requests = []
    for index, scenario in enumerate(G7_POSITIVE_SCENARIOS):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        request, _, registry = _fixture(case_root)
        result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
        assert result.plan is not None
        plan_path = Path(result.plan.path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        request_hash = canonical_json_sha256(request, "layout planning request")
        source_hash = canonical_json_sha256(
            request["source_dimension_request"], "source dimension planning request"
        )
        positive_cases.append(
            {
                "case_id": f"G7-{index}",
                "scenario": scenario,
                "plan_path": str(plan_path.resolve()),
                "plan_file_sha256": _sha(plan_path),
                "plan_canonical_sha256": canonical_json_sha256(
                    plan, "DrawingLayoutPlan"
                ),
                "planning_request": request,
                "planning_request_sha256": request_hash,
                "source_dimension_request_sha256": source_hash,
                "output_path": str((case_root / "qualified.SLDDRW").resolve()),
                "evidence_path": str((case_root / "g7-evidence.json").resolve()),
            }
        )
        requests.append(request)
    negative_request = copy.deepcopy(requests[0])
    negative_request["request_id"] = "DLPR-G7-UNAUTHORIZED"
    negative_request["plan_id"] = "DLP-G7-UNAUTHORIZED"
    negative_request["authorization"]["allowed_sheet_formats"] = []
    negative_root = tmp_path / "negative"
    negative_root.mkdir()
    negative_request["publication_directory"] = str(negative_root.resolve())
    matrix = {
        "protocol_id": "solidworks-drawing-layout-g7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "g0_qualification": {
            "path": str(qualification.resolve()),
            "sha256": _sha(qualification),
        },
        "positive_cases": positive_cases,
        "negative_cases": [
            {
                "case_id": "G7-UNAUTHORIZED",
                "scenario": G7_NEGATIVE_SCENARIOS[0],
                "planning_request": negative_request,
                "planning_request_sha256": canonical_json_sha256(
                    negative_request, "layout planning request"
                ),
                "expected_issue_code": "sheet-format-unauthorized",
                "evidence_path": str(
                    (negative_root / "g7-negative-evidence.json").resolve()
                ),
            }
        ],
    }

    normalized = validate_g7_matrix_request(matrix)
    assert len(normalized["positive_cases"]) == 9
    assert len(normalized["negative_cases"]) == 1
    duplicate = copy.deepcopy(matrix)
    duplicate["positive_cases"][1]["plan_path"] = duplicate["positive_cases"][0][
        "plan_path"
    ]
    duplicate["positive_cases"][1]["plan_file_sha256"] = duplicate[
        "positive_cases"
    ][0]["plan_file_sha256"]
    duplicate["positive_cases"][1]["plan_canonical_sha256"] = duplicate[
        "positive_cases"
    ][0]["plan_canonical_sha256"]
    duplicate["positive_cases"][1]["planning_request"] = duplicate[
        "positive_cases"
    ][0]["planning_request"]
    duplicate["positive_cases"][1]["planning_request_sha256"] = duplicate[
        "positive_cases"
    ][0]["planning_request_sha256"]
    duplicate["positive_cases"][1]["source_dimension_request_sha256"] = duplicate[
        "positive_cases"
    ][0]["source_dimension_request_sha256"]
    with pytest.raises(DrawingLayoutG7EvidenceError, match="distinct immutable plans"):
        validate_g7_matrix_request(duplicate)


@pytest.mark.parametrize(
    ("scenario", "dimension_count", "view_count", "view_types", "dimension_kinds", "operations"),
    [
        ("sparse_dimensions", 5, 1, {"model_view"}, {"linear"}, {"move_dimension"}),
        ("multi_view", 6, 3, {"model_view"}, {"linear"}, {"move_dimension"}),
        ("section_view", 6, 2, {"model_view", "full_section"}, {"linear"}, {"move_dimension"}),
        ("detail_view", 6, 2, {"model_view", "detail_view"}, {"linear"}, {"move_dimension"}),
        ("auxiliary_view", 6, 2, {"model_view", "auxiliary_view"}, {"linear"}, {"move_dimension"}),
        ("hole_pattern", 6, 2, {"model_view"}, {"hole_quantity", "hole_spacing"}, {"move_dimension"}),
        ("high_density_dimensions", 12, 2, {"model_view"}, {"linear"}, {"move_dimension"}),
        ("scale_change", 6, 2, {"model_view"}, {"linear"}, {"set_view_scale"}),
        ("authorized_sheet_format", 6, 2, {"model_view"}, {"linear"}, {"set_sheet_format"}),
    ],
)
def test_g7_scenario_label_requires_frozen_semantic_proof(
    tmp_path: Path,
    scenario: str,
    dimension_count: int,
    view_count: int,
    view_types: set[str],
    dimension_kinds: set[str],
    operations: set[str],
) -> None:
    view_plan = tmp_path / "view-plan.json"
    _write_json(
        view_plan,
        {"views": [{"type": item} for item in sorted(view_types)]},
    )
    dimension_plan = tmp_path / "dimension-plan.json"
    _write_json(
        dimension_plan,
        {
            "view_plan": {"path": str(view_plan.resolve()), "sha256": _sha(view_plan)},
            "dimensions": [{"kind": item} for item in sorted(dimension_kinds)],
        },
    )
    plan = {
        "source_dimension_plan": {"path": str(dimension_plan.resolve())},
        "source_invariants": {
            "dimension_ids": [f"D-{index}" for index in range(dimension_count)],
            "view_names": [f"View-{index}" for index in range(view_count)],
        },
        "operations": [{"kind": item} for item in sorted(operations)],
    }
    _validate_scenario_binding(scenario, plan, 0)

    failed = copy.deepcopy(plan)
    failed["source_invariants"]["dimension_ids"] = [f"D-{i}" for i in range(6)]
    failed["source_invariants"]["view_names"] = ["View-0", "View-1"]
    failed["operations"] = [{"kind": "move_dimension"}]
    if scenario in {"section_view", "detail_view", "auxiliary_view", "hole_pattern"}:
        empty_view = tmp_path / "empty-view.json"
        _write_json(empty_view, {"views": [{"type": "model_view"}]})
        empty_dimension = tmp_path / "empty-dimension.json"
        _write_json(
            empty_dimension,
            {
                "view_plan": {
                    "path": str(empty_view.resolve()),
                    "sha256": _sha(empty_view),
                },
                "dimensions": [{"kind": "linear"}],
            },
        )
        failed["source_dimension_plan"]["path"] = str(empty_dimension.resolve())
    with pytest.raises(DrawingLayoutG7EvidenceError, match="does not prove scenario"):
        _validate_scenario_binding(scenario, failed, 0)


def test_g7_negative_evidence_requires_deterministic_unauthorized_rejection() -> None:
    case = {
        "case_id": "G7-NEG",
        "scenario": "unauthorized_sheet_format",
        "planning_request_sha256": "a" * 64,
        "expected_issue_code": "sheet-format-unauthorized",
    }
    report = build_g7_negative_case_evidence(
        case,
        {
            "status": "rejected",
            "plan": None,
            "planning_request_sha256": "a" * 64,
            "validation": {"issues": [{"code": "sheet-format-unauthorized"}]},
        },
    )
    assert report["evidence_kind"] == "negative"
    with pytest.raises(DrawingLayoutG7EvidenceError):
        build_g7_negative_case_evidence(
            case,
            {
                "status": "published",
                "plan": {},
                "planning_request_sha256": "a" * 64,
                "validation": {"issues": []},
            },
        )


def test_g7_promotion_requires_complete_immutable_summary(tmp_path: Path) -> None:
    scenarios = G7_POSITIVE_SCENARIOS + G7_NEGATIVE_SCENARIOS
    summary = {
        "protocol_id": "solidworks-drawing-layout-g7-live-matrix-summary",
        "schema_version": "1.0",
        "generated_at_utc": "2026-08-15T00:00:00Z",
        "solidworks_revision": "33.5.0",
        "g0_qualification": {"path": "C:\\fixture\\g0.json", "sha256": "a" * 64},
        "execution_service_sha256": "b" * 64,
        "capability_manifest_sha256": "c" * 64,
        "case_evidence": [
            {
                "case_id": f"G7-{index}",
                "scenario": scenario,
                "evidence_kind": "negative" if scenario in G7_NEGATIVE_SCENARIOS else "positive",
                "path": f"C:\\fixture\\evidence-{index}.json",
                "sha256": "d" * 64,
            }
            for index, scenario in enumerate(scenarios)
        ],
        "scenario_coverage": {scenario: 1 for scenario in scenarios},
        "operation_coverage": {
            name: 1
            for name in (
                "move_dimension", "move_annotation", "route_leader", "move_view",
                "set_dimension_hierarchy", "set_view_scale", "set_sheet_scale",
                "set_sheet_format",
            )
        },
        "safety_coverage": {
            name: 1
            for name in (
                "dimension_semantic_preservation", "view_semantic_preservation",
                "object_identity_preservation", "collision_readback",
                "save_reopen_layout_fingerprint", "authorized_sheet_change",
            )
        },
        "source_hashes_unchanged": True,
        "all_positive_cases_independently_verified": True,
        "all_negative_cases_rejected": True,
        "overall_status": "complete",
    }
    summary_path = tmp_path / "summary.json"
    _write_json(summary_path, summary)
    current_path = ROOT / "drawing_layout_planner" / "capabilities" / "plan-current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    before = copy.deepcopy(current)
    promoted = build_g7_capability_promotion_candidate(current, summary_path)
    assert current == before
    assert all(row["status"] == "supported" for row in promoted["operations"].values())
    assert all(
        row["status"] == "supported" for row in promoted["safety_elements"].values()
    )

    summary["overall_status"] = "incomplete"
    incomplete = tmp_path / "incomplete.json"
    _write_json(incomplete, summary)
    with pytest.raises(DrawingLayoutG7EvidenceError, match="incomplete G7 evidence"):
        build_g7_capability_promotion_candidate(current, incomplete)


def test_g7_qualification_executor_routes_remain_private() -> None:
    controller = (
        ROOT
        / "solidworks-execution"
        / "SolidworksExecution"
        / "Controllers"
        / "ToolController.cs"
    ).read_text(encoding="utf-8-sig")
    service = (
        ROOT
        / "solidworks-execution"
        / "SolidworksExecution"
        / "Services"
        / "SolidWorksService.DrawingLayoutPlanExecution.cs"
    ).read_text(encoding="utf-8-sig")
    assert 'case "qualify_part_drawing_layout_plan"' in controller
    assert 'case "verify_qualified_part_drawing_layout_plan"' in controller
    assert "TryValidateQualification" in service
    assert '"g7_live_evidence_only"' in service
