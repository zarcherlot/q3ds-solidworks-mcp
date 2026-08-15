from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "drawing_layout_planner"


def test_g5_locks_the_strict_g4_verification_schema_hash() -> None:
    schema = PACKAGE / "contracts" / "drawing-layout-verification.schema.json"
    expected = hashlib.sha256(schema.read_bytes()).hexdigest()
    preflight = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Contracts/"
        "DrawingLayoutPlanVerificationPreflight.cs"
    ).read_text(encoding="utf-8-sig")
    assert expected in preflight
    parsed = json.loads(schema.read_text(encoding="utf-8"))
    required = parsed["$defs"]["verification"]["required"]
    assert "dimension_semantics" in required
    assert "view_semantics" in required
    assert "layout_fingerprint_sha256" in required


def test_g5_private_verify_route_does_not_expand_semantic_surface() -> None:
    controller = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Controllers/ToolController.cs"
    ).read_text(encoding="utf-8-sig")
    assert 'case "verify_committed_part_drawing_layout_plan"' in controller
    assert (
        'case "verify_committed_part_drawing_layout_plan": return ManagedSemanticTask'
        in controller
    )
    semantic_server = (ROOT / "adapters/claude/server.py").read_text(encoding="utf-8")
    assert "def verify_committed_part_drawing_layout_plan(" not in semantic_server
    assert "verify_committed_part_drawing_layout_plan" not in (
        ROOT / "adapters/claude/contracts/semantic-tools.schema.json"
    ).read_text(encoding="utf-8")


def test_g5_independently_rechecks_dimension_and_layout_native_state() -> None:
    verifier = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "DrawingLayoutPlanDrawingVerifier.cs"
    ).read_text(encoding="utf-8-sig")
    assert "_dimensions.TryVerifyPersisted" in verifier
    assert "_layout.TryVerifyPersisted" in verifier
    assert "swOpenDocOptions_ReadOnly" in verifier
    assert "ValidateCompleteIdentity" in verifier
    assert "DRAWING_LAYOUT_DANGLING_LEADER" in verifier
    assert "DRAWING_LAYOUT_OUTPUT_CHANGED_DURING_VERIFICATION" in verifier
    assert "Save3(" not in verifier


def test_g5_revalidates_after_read_only_close_and_does_not_increment_state() -> None:
    verifier = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "DrawingLayoutPlanDrawingVerifier.cs"
    ).read_text(encoding="utf-8-sig")
    service = (
        ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "SolidWorksService.DrawingLayoutPlanExecution.cs"
    ).read_text(encoding="utf-8-sig")
    assert verifier.count("DrawingLayoutPlanVerificationPreflight().TryValidate") == 2
    verify_method = service.split(
        "public ExecutionResponse VerifyCommittedPartDrawingLayoutPlan", 1
    )[1].split("private static bool TryParseDrawingLayoutRequest", 1)[0]
    assert "int state = _guard.GetCurrentStateVersion();" in verify_method
    assert "RegisterCompleted" not in verify_method
    assert "state + 1" not in verify_method


def test_g5_production_capabilities_are_promoted_only_with_g7_live_evidence() -> None:
    manifest = json.loads(
        (PACKAGE / "capabilities" / "plan-current.json").read_text(encoding="utf-8")
    )
    assert all(row["status"] == "supported" for row in manifest["operations"].values())
    assert all(
        row["status"] == "supported" for row in manifest["safety_elements"].values()
    )
    assert {
        row["evidence_sha256"] for row in manifest["safety_elements"].values()
    } == {"91e95b5c34ad92ac422839d6eb5585983336117bae7dbfc113f8e68be1122ecc"}
