from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_f4_csharp_contract_is_hash_locked_and_deployed() -> None:
    schema = ROOT / "dimension_planner/contracts/dimension-plan.schema.json"
    digest = hashlib.sha256(schema.read_bytes()).hexdigest()
    contract = _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanContractValidator.cs"
    )
    project = _text(
        "solidworks-execution/SolidworksExecution/SolidworksExecution.csproj"
    )
    assert digest in contract
    assert "dimension-plan.schema.json" in project
    assert "dimension-executor-capabilities.json" in project
    runtime_builder = _text("scripts/build_view_plan_live_runtime.ps1")
    assert "dimension-plan.schema.json" in runtime_builder
    assert "dimension-executor-capabilities.json" in runtime_builder
    assert "sourceRoot 'obj'" in runtime_builder
    verification_schema = json.loads(
        (ROOT / "dimension_planner/contracts/dimension-drawing-verification.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(verification_schema)


def test_f4_compiler_is_fail_closed_and_never_uses_legacy_drawing_plan() -> None:
    compiler = _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanExecutionCompiler.cs"
    )
    assert '"linear", "diameter", "radius", "angular", "reference"' in compiler
    assert '"hole_diameter", "hole_depth", "hole_quantity"' in compiler
    assert "DIMENSION_CAPABILITY_BLOCKED" in compiler
    assert "ExecuteDrawingPlan" not in compiler
    assert "ViewPlanBasicExecutionPlan" not in compiler
    assert "backing-face silhouette" in compiler


def test_f4_transaction_is_no_overwrite_save_reopen_and_exact_count() -> None:
    transaction = _text(
        "solidworks-execution/SolidworksExecution/Services/DimensionPlanDrawingTransaction.cs"
    )
    native = _text(
        "solidworks-execution/SolidworksExecution/Services/DimensionPlanNativeExecutor.cs"
    )
    assert "CopyDocument" in transaction
    assert "Save3" in transaction
    assert "swOpenDocOptions_ReadOnly" in transaction
    assert "File.Move(temporaryDrawing" in transaction
    assert "FrozenHashesEqual" in transaction
    assert "DIMENSION_UNPLANNED_OR_PARTIAL" in native
    assert "DeleteUnplannedImported" in native
    assert "DIMENSION_ATTACHMENT_MISMATCH" in native
    assert "DIMENSION_TEXT_MISMATCH" in native
    assert "DIMENSION_POSITION_MISMATCH" in native


def test_f4_private_operations_are_not_agent_visible() -> None:
    controller = _text(
        "solidworks-execution/SolidworksExecution/Controllers/ToolController.cs"
    )
    semantic_server = _text("adapters/claude/server.py")
    for operation in (
        "validate_frozen_part_drawing_dimension_plan",
        "execute_part_drawing_dimension_plan",
    ):
        assert operation in controller
        assert operation not in semantic_server


def test_f4_capabilities_remain_blocked_without_live_evidence() -> None:
    registry = json.loads(
        (ROOT / "dimension_planner/capabilities/current.json").read_text(encoding="utf-8")
    )
    for kind in (
        "linear",
        "diameter",
        "radius",
        "angular",
        "reference",
        "hole_diameter",
        "hole_depth",
        "hole_quantity",
    ):
        assert registry["dimension_types"][kind]["status"] == "planned"
        assert registry["dimension_types"][kind]["evidence_sha256"] is None
    capability = _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanCapabilityPreflight.cs"
    )
    assert 'item.Value<string>("status") == "supported"' in capability
    assert 'item.Value<string>("verification") == "live"' in capability
