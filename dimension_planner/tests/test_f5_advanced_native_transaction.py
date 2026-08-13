from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_f5_compiler_covers_complete_frozen_dimension_kind_union() -> None:
    schema = json.loads(
        (ROOT / "dimension_planner/contracts/dimension-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    kinds = set(schema["$defs"]["dimensionKind"]["enum"])
    compiler = _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanExecutionCompiler.cs"
    )
    assert len(kinds) == 18
    assert all(f'"{kind}"' in compiler for kind in kinds)
    assert "TwoAttachmentKinds" in compiler
    assert "Baseline dimensions must share one frozen first/datum attachment" in compiler
    assert "UseOrdinate" in compiler


def test_f5_native_executor_uses_advanced_solidworks_apis() -> None:
    native = _text(
        "solidworks-execution/SolidworksExecution/Services/DimensionPlanNativeExecutor.cs"
    )
    for api in (
        "AddChamferDim",
        "AddOrdinateDimension2",
        "AddHoleCallout2",
        "AddDiameterDimension2",
        "AddRadialDimension2",
        "AddSymmetricDimension",
        "IDimensionTolerance",
        "SetFitValues",
        "SetValues2",
        "GetHoleCalloutVariables",
    ):
        assert api in native
    assert "PersistenceFingerprint" in native
    assert "DIMENSION_TOLERANCE_MISMATCH" in native
    assert "DIMENSION_CHAIN_DISPLAY_MISMATCH" in native
    assert "swCreateOrdDimErr_Success" in native
    assert "model.SetPickMode()" in native
    assert 'kind == "hole_spacing"' in native
    assert "JToken.FromObject(item)" not in native


def test_f5_csharp_preflight_rebinds_every_trusted_tolerance_value() -> None:
    preflight = _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanTransactionPreflight.cs"
    )
    assert "ApprovedQuantityMatches" in preflight
    assert "ApprovedQuantityValue" in preflight
    assert "Fit code is absent from exact approved text inputs" in preflight
    assert "DIMENSION_TOLERANCE_UNTRUSTED" in preflight
    assert "FitTarget" in preflight
    assert "OrdinateType" in preflight


def test_f5_verification_sidecar_contract_includes_advanced_readback() -> None:
    schema_path = (
        ROOT / "dimension_planner/contracts/dimension-drawing-verification.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    required = set(schema["$defs"]["dimension"]["required"])
    assert {
        "text",
        "hole_callout_variables",
        "tolerance",
        "display_as_chain",
        "model_persistent_references",
    } <= required
    tolerance = schema["$defs"]["nativeTolerance"]
    assert {"minimum_si", "maximum_si", "hole_fit", "shaft_fit"} <= set(
        tolerance["required"]
    )
    hole_variable = schema["$defs"]["nativeHoleCalloutVariable"]
    assert {"variable_name", "value_kind", "value", "tolerance_type"} <= set(
        hole_variable["required"]
    )


def test_f5_capabilities_remain_planned_until_live_persisted_evidence() -> None:
    registry = json.loads(
        (ROOT / "dimension_planner/capabilities/current.json").read_text(encoding="utf-8")
    )
    assert registry["registry_version"] == "0.3.0"
    assert registry["executor_version"] == "0.2.0"
    advanced = {
        "aligned",
        "hole_spacing",
        "hole_group_location",
        "overall",
        "step",
        "boss",
        "slot",
        "chamfer",
        "fillet",
        "symmetric",
    }
    for kind in advanced:
        row = registry["dimension_types"][kind]
        assert row["status"] == "planned"
        assert row["verification"] == "none"
        assert row["evidence_sha256"] is None
    tolerance = registry["elements"]["dimension_tolerance"]
    assert tolerance["status"] == "planned"
    assert tolerance["evidence_sha256"] is None


def test_f5_executor_entries_remain_private() -> None:
    controller = _text(
        "solidworks-execution/SolidworksExecution/Controllers/ToolController.cs"
    )
    semantic_contract = json.loads(
        _text("adapters/claude/contracts/semantic-tools.schema.json")
    )["properties"]
    operation = "execute_part_drawing_dimension_plan"
    assert operation in controller
    assert operation not in semantic_contract
    assert "ExecuteDrawingPlan" not in _text(
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanExecutionCompiler.cs"
    )
