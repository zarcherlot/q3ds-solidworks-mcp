from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dimension_planner.capability_registry import (
    DimensionCapabilityManifest,
    DimensionCapabilityRegistry,
    DimensionExecutionCapabilityError,
    current_registry,
)
from dimension_planner.planner_engine import DimensionPlannerEngine
from dimension_planner.planning_models import DimensionPlanningRequest
from dimension_planner.validators import RepositoryDimensionPlanValidator


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[dict, DimensionPlanningRequest, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / "part.SLDPRT"
    drawing = tmp_path / "views.SLDDRW"
    view_plan = tmp_path / "view_plan.json"
    sidecar = tmp_path / "views.verify.json"
    model.write_bytes(b"model")
    drawing.write_bytes(b"drawing")
    _write(view_plan, {"protocol_id": "solidworks-view-plan", "schema_version": "1.4"})
    _write(sidecar, {"verified": True})
    artifacts = []
    for role, path in (
        ("view_plan", view_plan),
        ("verified_drawing", drawing),
        ("verification_sidecar", sidecar),
        ("source_model", model),
    ):
        digest = _sha(path)
        artifacts.append(
            {
                "role": role,
                "path": str(path.resolve()),
                "sha256_before": digest,
                "sha256_after": digest,
            }
        )
    handoff = {
        "protocol_id": "solidworks-dimension-planning-handoff",
        "schema_version": "1.0",
        "handoff_id": "DMH-F3",
        "created_at_utc": "2026-08-13T03:00:00Z",
        "status": "ready",
        "source_request_sha256": "a" * 64,
        "upstream_artifacts": artifacts,
        "source_model": {
            "path": str(model.resolve()),
            "sha256": _sha(model),
            "configuration": "Default",
            "save_flag": False,
            "persistent_reference_domain": "source_model",
        },
        "drawing_context": {
            "path": str(drawing.resolve()),
            "sheet_name": "Sheet1",
            "sheet_bounds_m": [0.0, 0.0, 0.42, 0.297],
            "projection_method": "third_angle",
            "sheet_scale": 1.0,
        },
        "views": [
            {
                "view_id": "front",
                "solidworks_name": "Q3DS_VP_front",
                "bounds_sheet_m": [0.1, 0.1, 0.2, 0.2],
                "referenced_model": str(model.resolve()),
                "configuration": "Default",
                "projected_geometry": [
                    {
                        "entity_id": "GE-1",
                        "entity_kind": "line",
                        "model_persistent_reference": "AQID",
                        "persistent_reference_kind": "entity",
                        "geometry_sheet_m": [0.1, 0.1, 0.2, 0.1],
                        "source_tier": "model_or_pmi",
                    },
                    {
                        "entity_id": "GE-2",
                        "entity_kind": "line",
                        "model_persistent_reference": "BAUG",
                        "persistent_reference_kind": "entity",
                        "geometry_sheet_m": [0.1, 0.2, 0.2, 0.2],
                        "source_tier": "model_or_pmi",
                    },
                    {
                        "entity_id": "GE-3",
                        "entity_kind": "line",
                        "model_persistent_reference": "BwgJ",
                        "persistent_reference_kind": "entity",
                        "geometry_sheet_m": [0.1, 0.1, 0.1, 0.2],
                        "source_tier": "model_or_pmi",
                    },
                ],
                "existing_annotations": [],
            }
        ],
        "model_driven_dimensions": [
            {
                "dimension_id": "MD-1",
                "full_name": "D1@Sketch1@part.SLDPRT",
                "value_si": 0.01,
                "source_tier": "model_or_pmi",
                "provenance": "model_driven_dimension",
            }
        ],
        "pmi_annotations": [],
        "manufacturing_features": [
            {
                "feature_id": "MF-1",
                "name": "Hole1",
                "type_name": "HoleWzd",
                "classification": "hole",
                "persistent_reference": "Cg==",
                "source_tier": "model_or_pmi",
            }
        ],
        "approved_user_inputs": [],
        "reference_measurements": [],
        "dimension_zones": [
            {
                "id": "DZ-front-top",
                "view_id": "front",
                "side": "top",
                "dimension_layers": 1,
                "required_depth_m": 0.05,
                "bounds_sheet_m": [0.05, 0.22, 0.25, 0.27],
            }
        ],
        "limitations": {
            "annotation_text_bounds": "unsupported_exact",
            "reference_measurements_are_manufacturing_requirements": False,
        },
        "source_immutability": {
            "drawing_opened_read_only": True,
            "source_documents_clean": True,
            "hashes_unchanged": True,
        },
    }
    handoff_path = tmp_path / "dimension-planning-handoff.json"
    _write(handoff_path, handoff)
    ledger = {row["role"]: row for row in artifacts}
    plan = {
        "$schema": "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json",
        "protocol_id": "solidworks-dimension-plan",
        "schema_version": "1.0",
        "plan_id": "DP-F3",
        "created_at_utc": "2026-08-13T03:01:00Z",
        "producer": {
            "name": "dimension-planner",
            "version": "1.0.0",
            "ruleset_id": "dimension-native-v1",
            "ruleset_sha256": "b" * 64,
        },
        "execution_policy": {
            "on_integrity_mismatch": "fail",
            "on_selection_ambiguity": "fail",
            "on_unsupported_dimension": "fail",
            "on_layout_violation": "fail",
            "allow_source_model_write": False,
            "allow_upstream_drawing_overwrite": False,
            "allow_partial_commit": False,
        },
        "handoff": {"path": str(handoff_path.resolve()), "sha256": _sha(handoff_path)},
        "handoff_id": handoff["handoff_id"],
        "source_model": {
            "path": handoff["source_model"]["path"],
            "sha256": handoff["source_model"]["sha256"],
        },
        "source_drawing": {
            "path": ledger["verified_drawing"]["path"],
            "sha256": ledger["verified_drawing"]["sha256_before"],
        },
        "view_plan": {
            "path": ledger["view_plan"]["path"],
            "sha256": ledger["view_plan"]["sha256_before"],
        },
        "verification_sidecar": {
            "path": ledger["verification_sidecar"]["path"],
            "sha256": ledger["verification_sidecar"]["sha256_before"],
        },
        "configuration": "Default",
        "dimensions": [_dimension()],
        "assumptions": [],
        "open_questions": [],
    }
    request = DimensionPlanningRequest(
        handoff_path=str(handoff_path.resolve()),
        handoff_sha256=_sha(handoff_path),
        publication_directory=str(tmp_path.resolve()),
    )
    return plan, request, handoff


def _dimension(index: int = 1) -> dict:
    return {
        "dimension_id": f"D-{index}",
        "kind": "linear",
        "source": {
            "source_tier": "model_or_pmi",
            "handoff_collection": "model_driven_dimensions",
            "source_ids": ["MD-1"],
        },
        "target_view_id": "front",
        "attachments": [
            {
                "attachment_id": f"A-{index}-1",
                "entity_id": "GE-1",
                "model_persistent_reference": "AQID",
                "persistent_reference_kind": "entity",
                "role": "first",
            },
            {
                "attachment_id": f"A-{index}-2",
                "entity_id": "GE-2",
                "model_persistent_reference": "BAUG",
                "persistent_reference_kind": "entity",
                "role": "second",
            },
        ],
        "feature_ids": ["MF-1"],
        "value": {
            "value_mode": "model_driven",
            "quantity_kind": "length",
            "nominal_si": 0.01,
        },
        "tolerance": None,
        "display_format": {
            "unit": "mm",
            "precision": 2,
            "prefix": "",
            "suffix": "",
            "show_parentheses": False,
            "show_units": False,
            "dual_units": False,
        },
        "dimension_zone_id": "DZ-front-top",
        "hierarchy": {
            "level": "manufacturing",
            "priority": index,
            "chain_id": None,
            "baseline_id": None,
        },
        "initial_position_sheet_m": [0.15 + index * 0.005, 0.24],
        "verification_tolerance": {
            "value_abs_si": 1e-9,
            "position_abs_m": 1e-6,
            "attachment_count_exact": True,
            "display_text_exact": False,
        },
    }


def _validate(tmp_path: Path, mutate=None):
    plan, request, handoff = _fixture(tmp_path)
    if mutate:
        mutate(plan, handoff)
    return RepositoryDimensionPlanValidator().validate(plan, request)


def _republish_handoff(plan, request, handoff):
    handoff_path = Path(request.handoff_path)
    _write(handoff_path, handoff)
    request = request.model_copy(update={"handoff_sha256": _sha(handoff_path)})
    plan["handoff"]["sha256"] = request.handoff_sha256
    return request


def test_engineering_valid_plan_publishes_as_capability_blocked(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    result = DimensionPlannerEngine().validate_and_publish(plan, request)
    assert result.status == "published"
    assert result.execution_readiness == "capability_blocked"
    assert result.validation.engineering_passed is True
    assert result.validation.capability == "fail"
    assert Path(result.plan.path).is_file()
    with pytest.raises(DimensionExecutionCapabilityError, match="capability_blocked"):
        current_registry().require_supported(plan)
    with pytest.raises(FileExistsError):
        DimensionPlannerEngine().validate_and_publish(plan, request)


def test_supported_capabilities_produce_executable_publication(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    manifest_value = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "capabilities"
            / "current.json"
        ).read_text(encoding="utf-8")
    )
    evidence = manifest_value["live_evidence"]["summary_sha256"]
    manifest_value["dimension_types"]["linear"].update(
        status="supported", verification="live", evidence_sha256=evidence
    )
    for name in (
        "model_dimension_import",
        "attachment_persistent_reference",
        "annotation_position",
        "save_reopen_stable_identity",
    ):
        manifest_value["elements"][name].update(
            status="supported", verification="live", evidence_sha256=evidence
        )
    capabilities = DimensionCapabilityRegistry(
        DimensionCapabilityManifest.model_validate(manifest_value)
    )
    result = DimensionPlannerEngine(capabilities=capabilities).validate_and_publish(
        plan, request
    )
    assert result.status == "published"
    assert result.execution_readiness == "supported"
    assert result.validation.capability == "pass"
    capabilities.require_supported(plan)


def test_integrity_failure_short_circuits_every_later_gate(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    plan["handoff"]["sha256"] = "0" * 64
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.integrity == "fail"
    assert result.schema_check == result.source == result.capability == "not_run"


def test_schema_failure_short_circuits_domain_gates(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    del plan["dimensions"][0]["kind"]
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.integrity == "pass"
    assert result.schema_check == "fail"
    assert result.source == result.capability == "not_run"


def test_source_gate_rejects_fabricated_nominal_and_tolerance(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    dimension = plan["dimensions"][0]
    dimension["value"]["nominal_si"] = 0.02
    dimension["tolerance"] = {
        "kind": "bilateral",
        "lower_si": -0.001,
        "upper_si": 0.001,
        "fit_code": None,
    }
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.source == "fail"
    assert {row.code for row in result.issues} == {
        "DP-SOURCE-NOMINAL-UNBOUND",
        "DP-SOURCE-TOLERANCE-UNTRUSTED",
    }
    assert result.attachment == result.capability == "not_run"


def test_source_gate_accepts_explicitly_approved_tolerance(tmp_path: Path) -> None:
    plan, request, handoff = _fixture(tmp_path)
    values = (
        ("APP-NOMINAL", 0.01),
        ("APP-LOWER", -0.001),
        ("APP-UPPER", 0.001),
    )
    handoff["approved_user_inputs"] = [
        {
            "input_id": input_id,
            "source_tier": "user_confirmed_input",
            "approved_by": "reviewer",
            "approved_at_utc": "2026-08-13T02:00:00Z",
            "approval_reference": "F3-test",
            "target_feature_ids": ["MF-1"],
            "value": {
                "kind": "quantity",
                "quantity_kind": "length",
                "value_si": value,
            },
        }
        for input_id, value in values
    ]
    dimension = plan["dimensions"][0]
    dimension["source"] = {
        "source_tier": "user_confirmed_input",
        "approved_input_ids": [row[0] for row in values],
    }
    dimension["value"]["value_mode"] = "approved_value"
    dimension["tolerance"] = {
        "kind": "bilateral",
        "lower_si": -0.001,
        "upper_si": 0.001,
        "fit_code": None,
    }
    request = _republish_handoff(plan, request, handoff)
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.source == "pass"
    assert result.engineering_passed is True


def test_reference_measurement_remains_reference_only(tmp_path: Path) -> None:
    plan, request, handoff = _fixture(tmp_path)
    handoff["reference_measurements"] = [
        {
            "measurement_id": "RM-1",
            "view_id": "front",
            "kind": "projected_line_length",
            "value_si": 0.02,
            "entity_ids": ["GE-1"],
            "source_tier": "reference_geometry_measurement",
            "manufacturing_requirement": False,
        }
    ]
    reference = _dimension(2)
    reference.update(
        dimension_id="REF-1",
        kind="reference",
        source={
            "source_tier": "reference_geometry_measurement",
            "measurement_ids": ["RM-1"],
            "manufacturing_requirement": False,
        },
        attachments=[reference["attachments"][0]],
    )
    reference["value"].update(
        value_mode="measured_reference",
        nominal_si=0.02,
    )
    reference["hierarchy"]["level"] = "reference"
    reference["display_format"]["show_parentheses"] = True
    reference["initial_position_sheet_m"] = [0.2, 0.24]
    plan["dimensions"].append(reference)
    request = _republish_handoff(plan, request, handoff)
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.source == result.attachment == result.semantics == "pass"
    assert result.engineering_passed is True


def test_attachment_gate_rejects_invisible_entity(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    plan["dimensions"][0]["attachments"][1]["entity_id"] = "GE-hidden"
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.source == "pass"
    assert result.attachment == "fail"
    assert result.semantics == "not_run"


def test_native_model_import_allows_single_resolved_attachment_identity(
    tmp_path: Path,
) -> None:
    plan, request, _ = _fixture(tmp_path)
    second = plan["dimensions"][0]["attachments"][1]
    second.update(
        entity_id="GE-1",
        model_persistent_reference="AQID",
    )
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.attachment == "pass"
    assert result.semantics == "pass"


def test_coverage_gate_rejects_unexpressed_manufacturing_feature(tmp_path: Path) -> None:
    plan, request, handoff = _fixture(tmp_path)
    handoff["manufacturing_features"].append(
        {**handoff["manufacturing_features"][0], "feature_id": "MF-2", "name": "Hole2"}
    )
    handoff_path = Path(request.handoff_path)
    _write(handoff_path, handoff)
    request = request.model_copy(update={"handoff_sha256": _sha(handoff_path)})
    plan["handoff"]["sha256"] = request.handoff_sha256
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.semantics == "pass"
    assert result.coverage == "fail"
    assert result.issues[0].code == "DP-COVERAGE-FEATURE-MISSING"


def test_coverage_gate_rejects_omitted_model_dimension_marked_for_drawing(
    tmp_path: Path,
) -> None:
    plan, request, handoff = _fixture(tmp_path)
    handoff["model_driven_dimensions"][0].update(
        native_type=2,
        marked_for_drawing=True,
        reference_dimension=False,
        manufacturing_requirement=True,
        owner_feature_id="MF-1",
        owner_feature_name="Sketch1",
        owner_feature_type="ProfileFeature",
        owner_feature_persistent_reference="Cg==",
    )
    handoff["model_driven_dimensions"].append(
        {
            **handoff["model_driven_dimensions"][0],
            "dimension_id": "MD-2",
            "full_name": "D2@Sketch1@part.SLDPRT",
            "value_si": 0.02,
        }
    )
    request = _republish_handoff(plan, request, handoff)

    result = RepositoryDimensionPlanValidator().validate(plan, request)

    assert result.semantics == "pass"
    assert result.coverage == "fail"
    assert [row.code for row in result.issues] == [
        "DP-COVERAGE-MODEL-DIMENSION-MISSING"
    ]


def test_redundancy_gate_rejects_duplicate_and_closed_chain(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    duplicate = _dimension(2)
    duplicate["initial_position_sheet_m"] = [0.18, 0.24]
    plan["dimensions"].append(duplicate)
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.coverage == "pass"
    assert result.redundancy == "fail"
    assert result.issues[0].code == "DP-REDUNDANCY-DUPLICATE"

    plan, request, _ = _fixture(tmp_path / "cycle")
    # A three-edge entity cycle is a forbidden closed dimension chain.
    for index, pair in enumerate((("GE-1", "GE-2"), ("GE-2", "GE-3"), ("GE-3", "GE-1")), 1):
        row = _dimension(index)
        row["dimension_id"] = f"CHAIN-{index}"
        row["attachments"][0].update(
            entity_id=pair[0],
            model_persistent_reference={"GE-1": "AQID", "GE-2": "BAUG", "GE-3": "BwgJ"}[pair[0]],
        )
        row["attachments"][1].update(
            entity_id=pair[1],
            model_persistent_reference={"GE-1": "AQID", "GE-2": "BAUG", "GE-3": "BwgJ"}[pair[1]],
        )
        row["hierarchy"]["chain_id"] = "CHAIN-A"
        row["initial_position_sheet_m"] = [0.1 + index * 0.03, 0.24]
        plan["dimensions"][index - 1 : index] = [row]
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.redundancy == "fail"
    assert any(row.code == "DP-REDUNDANCY-CLOSED-CHAIN" for row in result.issues)


def test_layout_gate_rejects_unstable_position(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    plan["dimensions"][0]["initial_position_sheet_m"] = [0.15, 0.15]
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.redundancy == "pass"
    assert result.layout == "fail"
    assert result.capability == "not_run"
    assert {row.code for row in result.issues} == {
        "DP-LAYOUT-POSITION-IN-VIEW",
        "DP-LAYOUT-UNSTABLE-POSITION",
    }


def test_layout_gate_accepts_viewplan_object_zone_bounds(tmp_path: Path) -> None:
    plan, request, handoff = _fixture(tmp_path)
    handoff["dimension_zones"][0]["bounds_sheet_m"] = {
        "x_min_m": 0.05,
        "y_min_m": 0.22,
        "x_max_m": 0.25,
        "y_max_m": 0.27,
    }
    request = _republish_handoff(plan, request, handoff)
    result = RepositoryDimensionPlanValidator().validate(plan, request)
    assert result.layout == "pass"
    assert result.engineering_passed is True


def test_rejected_engine_does_not_publish(tmp_path: Path) -> None:
    plan, request, _ = _fixture(tmp_path)
    plan["dimensions"][0]["source"]["source_ids"] = ["MD-missing"]
    result = DimensionPlannerEngine().validate_and_publish(plan, request)
    assert result.status == "rejected"
    assert result.execution_readiness == "not_assessed"
    assert not (tmp_path / "dimension_plan.json").exists()
