from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "drawing_layout_planner"
SCHEMA = PACKAGE / "contracts" / "drawing-layout-verification.schema.json"
SHA = "a" * 64


def test_g4_verification_schema_is_valid_and_accepts_bounded_sidecar() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    verification = {
        "verified": True,
        "dimension_semantics": {"count": 1},
        "view_semantics": [{"name": "Front"}],
        "layout_fingerprint_sha256": SHA,
        "snapshot": {},
    }
    candidate = {
        "protocol_id": "solidworks-drawing-layout-verification",
        "schema_version": "1.0",
        "operation_id": "G4-test",
        "generated_at_utc": "2026-08-15T12:00:00Z",
        "plan_id": "layout-plan-g4",
        "plan_file_path": "C:/fixture/drawing_layout_plan.json",
        "plan_file_sha256": SHA,
        "plan_canonical_sha256": SHA,
        "source_drawing_path": "C:/fixture/dimensioned.SLDDRW",
        "source_drawing_sha256": SHA,
        "output_path": "C:/fixture/final.SLDDRW",
        "artifact_sha256": SHA,
        "verified": True,
        "bounded_cycles": [{"cycle": 1, "verified": True}],
        "in_memory_verification": verification,
        "reopen_verification": verification,
        "frozen_inputs": {
            "drawing_layout_plan": SHA,
            "handoff": SHA,
            "dimension_plan": SHA,
            "source_drawing": SHA,
            "dimension_verification_sidecar": SHA,
            "dimension_plan.source_model": SHA,
        },
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(candidate)) == []
    candidate["verified"] = False
    assert list(validator.iter_errors(candidate))


def test_g4_private_routes_do_not_expand_semantic_mcp_surface() -> None:
    controller = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Controllers/ToolController.cs"
    ).read_text(encoding="utf-8")
    assert 'case "validate_frozen_part_drawing_layout_plan"' in controller
    assert 'case "execute_part_drawing_layout_plan"' in controller
    assert 'case "verify_committed_part_drawing_layout_plan"' in controller
    semantic_server = (ROOT / "adapters/claude/server.py").read_text(encoding="utf-8")
    for private in (
        "execute_part_drawing_layout_plan",
        "validate_frozen_part_drawing_layout_plan",
        "verify_committed_part_drawing_layout_plan",
    ):
        assert f"def {private}(" not in semantic_server
        assert private not in (
            ROOT / "adapters/claude/contracts/semantic-tools.schema.json"
        ).read_text(encoding="utf-8")


def test_g4_transaction_is_bounded_atomic_and_no_overwrite() -> None:
    source = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "DrawingLayoutPlanDrawingTransaction.cs"
    ).read_text(encoding="utf-8")
    native = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "DrawingLayoutPlanNativeExecutor.cs"
    ).read_text(encoding="utf-8")
    assert "CopyDocument(plan.SourceDrawing.Path, temporaryDrawing" in source
    assert "swOpenDocOptions_ReadOnly" in source
    assert "DRAWING_LAYOUT_OUTPUT_RACE" in source
    assert "File.Move(temporaryDrawing, paths.OutputPath)" in source
    assert "File.Move(temporaryReport, paths.ReportPath)" in source
    assert "private const int MaximumCycles = 3" in native
    assert "DRAWING_LAYOUT_ADJUSTMENT_LIMIT" in native
    assert "layout_fingerprint_sha256" in native


def test_g4_runtime_build_deploys_locked_contracts_and_capabilities() -> None:
    script = (ROOT / "scripts/build_view_plan_live_runtime.ps1").read_text(
        encoding="utf-8"
    )
    for name in (
        "drawing-layout-plan.schema.json",
        "drawing-layout-verification.schema.json",
        "drawing-layout-plan-capabilities.json",
        "drawing-layout-executor-capabilities.json",
    ):
        assert name in script


def test_g4_production_capabilities_bind_completed_g7_live_evidence() -> None:
    manifest = json.loads(
        (PACKAGE / "capabilities" / "plan-current.json").read_text(encoding="utf-8")
    )
    assert {row["status"] for row in manifest["operations"].values()} == {"supported"}
    assert {row["verification"] for row in manifest["operations"].values()} == {"live"}
    assert {row["status"] for row in manifest["safety_elements"].values()} == {"supported"}
